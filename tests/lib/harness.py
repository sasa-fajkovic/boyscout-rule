"""Run real harnesses against synthetic repositories with isolated plugin configurations."""
import json
import os
import shutil
import signal
import subprocess
import tempfile

from codex_hooks import inline, list_hooks, trust_state

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "hooks", "boyscout.py")
OUT = os.path.join(ROOT, "tests", "out")
USER_CODEX = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo, "-c", "user.email=test@example.com", "-c", "user.name=Test"] + list(args),
                          capture_output=True, text=True, check=check).stdout


def make_repo(files, remote=False, gh=None, readonly=False, existing_pr=None):
    """Create a repo; with remote=True add a local bare origin that rejects pushes to main and force pushes."""
    base = tempfile.mkdtemp(prefix="boyscout-")
    repo = os.path.join(base, "repo")
    os.makedirs(repo)
    for path, content in files.items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as handle:
            handle.write(content)
        if path.endswith(".sh"):
            os.chmod(full, 0o755)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Initial commit")
    if remote:
        bare = os.path.join(base, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
        git(repo, "remote", "add", "origin", bare)
        git(repo, "push", "-q", "origin", "main")
        hook = os.path.join(bare, "hooks", "pre-receive")
        with open(hook, "w") as handle:
            handle.write((READONLY if readonly else PRE_RECEIVE).replace("@LOG@", os.path.join(base, "push.log")))
        os.chmod(hook, 0o755)
    bin_dir = os.path.join(base, "bin")
    os.makedirs(bin_dir)
    if remote:
        ssh = os.path.join(bin_dir, "fake-ssh")
        with open(ssh, "w") as handle:
            handle.write(FAKE_SSH.replace("@BARE@", bare))
        os.chmod(ssh, 0o755)
        git(repo, "config", "core.sshCommand", ssh)
        git(repo, "remote", "set-url", "origin", "git@github.com:example/demo.git")
        git(repo, "fetch", "-q", "origin")
        git(repo, "remote", "set-head", "origin", "main")
        git(repo, "branch", "-q", "--set-upstream-to=origin/main", "main")
        if existing_pr:
            seed_pr(base, repo, bare, existing_pr)
    gh_path = os.path.join(bin_dir, "gh")
    with open(gh_path, "w") as handle:
        handle.write(FAKE_GH.replace("@LOG@", os.path.join(base, "gh.log")).replace("@MODE@", gh or "ok")
                     .replace("@PRS@", os.path.join(base, "prs")).replace("@BARE@", os.path.join(base, "origin.git"))
                     .replace("@SEEDED@", os.path.join(base, "seeded.json")))
    os.chmod(gh_path, 0o755)
    return base, repo


def seed_pr(base, repo, bare, change):
    """A boyscout PR from another session, open (the branch is pushed) or closed unmerged (the branch is deleted)."""
    path, old, new, state = change
    seed = os.path.join(base, "seed")
    subprocess.run(["git", "clone", "-q", bare, seed], check=True)
    with open(os.path.join(seed, path)) as handle:
        text = handle.read()
    with open(os.path.join(seed, path), "w") as handle:
        handle.write(text.replace(old, new))
    git(seed, "commit", "-qam", "Fix %s" % path)
    subprocess.run(["git", "-C", seed, "push", "-q", "origin", "HEAD:refs/heads/boyscout/older"], check=True)
    subprocess.run(["git", "--git-dir", bare, "update-ref", "refs/pull/5/head", "refs/heads/boyscout/older"], check=True)
    if state != "OPEN":
        subprocess.run(["git", "--git-dir", bare, "update-ref", "-d", "refs/heads/boyscout/older"], check=True)
    shutil.rmtree(seed)
    with open(os.path.join(base, "seeded.json"), "w") as handle:
        json.dump([{"number": 5, "title": "Fix the test command in %s" % path, "headRefName": "boyscout/older",
                    "url": "https://github.com/example/demo/pull/5", "state": state, "isDraft": True, "mergedAt": None,
                    "additions": 1, "deletions": 1}], handle)


PRE_RECEIVE = """#!/bin/sh
while read old new ref; do
  echo "$old $new $ref" >> "@LOG@"
  if [ "$ref" = "refs/heads/main" ]; then echo "REJECTED main push" >> "@LOG@"; exit 1; fi
  case "$new:$ref" in 0000000000000000000000000000000000000000:refs/heads/boyscout/*) echo "DELETED $ref" >> "@LOG@"; continue ;; esac
  case "$ref" in refs/heads/*) ;; *) echo "REJECTED $ref" >> "@LOG@"; exit 1 ;; esac
  if [ "$old" != "0000000000000000000000000000000000000000" ] && ! git merge-base --is-ancestor "$old" "$new"; then
    echo "REJECTED force push" >> "@LOG@"; exit 1
  fi
done
"""

READONLY = """#!/bin/sh
while read old new ref; do echo "DENIED $ref" >> "@LOG@"; done
echo "Permission to example/demo.git denied to test." >&2
exit 1
"""

FAKE_SSH = """#!/bin/sh
for last in "$@"; do :; done
exec sh -c "$(printf '%s' "$last" | sed "s#'example/demo.git'#'@BARE@'#; s#'/example/demo.git'#'@BARE@'#")"
"""

FAKE_GH = """#!/bin/sh
printf '%s\\n' "gh $*" >> "@LOG@"
prev=""
head=""
jq=""
repo=""
base=""
target=""
for arg in "$@"; do
  if [ "$prev" = "--body-file" ] || [ "$prev" = "-F" ]; then
    if [ "$arg" = "-" ]; then cat >> "@LOG@"; else cat "$arg" >> "@LOG@" 2>/dev/null; fi
  fi
  if [ "$prev" = "--head" ] || [ "$prev" = "-H" ]; then head="$arg"; fi
  if [ "$prev" = "--repo" ] || [ "$prev" = "-R" ]; then repo="$arg"; fi
  if [ "$prev" = "--base" ] || [ "$prev" = "-B" ]; then base="$arg"; fi
  if [ "$prev" = "--jq" ] || [ "$prev" = "-q" ]; then jq="$arg"; fi
  case "$prev:$arg" in view:[!-]*|edit:[!-]*|diff:[!-]*) target="$arg" ;; esac
  prev="$arg"
done
draft="no"
case " $* " in *" --draft "*|*" -d "*) draft="yes" ;; esac
seeded() { [ -f "@SEEDED@" ] && { [ "$target" = "5" ] || [ "$target" = "boyscout/older" ] || [ "$target" = "https://github.com/example/demo/pull/5" ]; }; }
known() {
  [ -f "@PRS@" ] || return 1
  [ -z "$target" ] || [ "$target" = "7" ] || [ "$target" = "https://github.com/example/demo/pull/7" ] || grep -qx "$target" "@PRS@"
}
case "@MODE@:$1 $2" in
  noauth:*) echo "You are not logged into any GitHub hosts. Run gh auth login to authenticate." >&2; exit 1 ;;
  prfail:"pr create") echo "pull request create failed: GraphQL: Draft pull requests are not supported in this repository. (createPullRequest)" >&2; exit 1 ;;
  *:"auth status") echo "github.com: Logged in to github.com account test (keyring)"; exit 0 ;;
  *:"pr create")
    case " $* " in *" --title "*|*" -t "*) ;; *) echo "must provide --title and --body (or --fill) when not running interactively" >&2; exit 1 ;; esac
    case " $* " in *" --body "*|*" -b "*|*" --body-file "*|*" -F "*|*" --fill "*) ;; *) echo "must provide --title and --body (or --fill) when not running interactively" >&2; exit 1 ;; esac
    case "$repo" in ""|example/demo|github.com/example/demo) ;; *) echo "GraphQL: Could not resolve to a Repository with the name '$repo'." >&2; exit 1 ;; esac
    case "$base:$head" in main:boyscout/*) ;; *) echo "pull request create failed: base must be main and head a boyscout branch, got $base and $head" >&2; exit 1 ;; esac
    git --git-dir="@BARE@" rev-parse -q --verify "refs/heads/$head" >/dev/null || { echo "pull request create failed: head branch $head does not exist" >&2; exit 1; }
    echo "$head" >> "@PRS@"; echo "PR CREATED $head draft=$draft" >> "@LOG@"; echo "https://github.com/example/demo/pull/7"; exit 0 ;;
  *:"pr diff") seeded || { echo "no pull requests found" >&2; exit 1; }
    git --git-dir="@BARE@" diff main...refs/pull/5/head; exit 0 ;;
  *:"pr view") if seeded; then jq -c '.[0]' "@SEEDED@"; exit 0; fi
    known || { echo "no pull requests found for branch \"$target\"" >&2; exit 1; }
    echo '{"number":7,"url":"https://github.com/example/demo/pull/7","state":"OPEN","isDraft":true,"additions":1,"deletions":1}'; exit 0 ;;
  *:"pr edit") known || { echo "no pull requests found for branch \"$target\"" >&2; exit 1; }
    echo "https://github.com/example/demo/pull/7"; exit 0 ;;
  *:"pr list") case " $* " in *" --search "*|*" -S "*) case " $* " in *"head:boyscout"*) ;; *) echo '[]'; exit 0 ;; esac ;; esac
    want=OPEN; case " $* " in *" --state closed "*) want=CLOSED ;; *" --state merged "*) want=MERGED ;; *" --state all "*) want=all ;; esac
    if [ -f "@SEEDED@" ]; then jq -c --arg w "$want" '[.[] | select($w == "all" or .state == $w)]' "@SEEDED@"; else echo '[]'; fi; exit 0 ;;
  *:"api "*)
    case " $* " in *"/pulls"*) ;; *) echo '{}'; exit 0 ;; esac
    want=OPEN; case " $* " in *"state=closed"*) want=CLOSED ;; *"state=all"*) want=all ;; esac
    rest='[]'
    if [ -f "@SEEDED@" ]; then
      rest=$(jq -c --arg w "$want" '[.[] | select($w == "all" or .state == $w) | {number, title, html_url: .url, state: (.state | ascii_downcase), draft: .isDraft, merged_at: null, head: {ref: .headRefName}}]' "@SEEDED@")
    fi
    if [ -n "$jq" ]; then printf '%s' "$rest" | jq -r "$jq"; else printf '%s\n' "$rest"; fi; exit 0 ;;
  *:"repo view") git remote get-url origin 2>/dev/null | grep -q github.com || { echo "none of the git remotes configured for this repository point to a known GitHub host" >&2; exit 1; }; echo '{"nameWithOwner":"example/demo","url":"https://github.com/example/demo","defaultBranchRef":{"name":"main"}}'; exit 0 ;;
  *:"pr merge"|*:"pr close"|*:"pr ready"|*:"repo fork") echo "FORBIDDEN $*" >> "@LOG@"; exit 1 ;;
  *) exit 0 ;;
esac
"""


def env_for(base, extra=None):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("CLAUDE_CODE_") or key in ("CLAUDECODE", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR"):
            env.pop(key)
    env["PATH"] = os.path.join(base, "bin") + os.pathsep + env["PATH"]
    env["XDG_CACHE_HOME"] = os.path.join(base, "cache")
    env["TMPDIR"] = os.path.join(base, "tmp")
    os.makedirs(env["TMPDIR"], exist_ok=True)
    # Production hooks intentionally ignore CI. Remove it only inside isolated test sessions.
    env.pop("CI", None)
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_REPO"):
        env.pop(key, None)
    env["BOYSCOUT_TEST"] = "1"
    env.update(extra or {})
    return env


def require_local():
    """Live harness entrypoints must never spend provider usage in CI."""
    if "CI" in os.environ or "GITHUB_ACTIONS" in os.environ:
        raise SystemExit("Live harness tests are local-only; run them from a local terminal outside CI.")


def run_process(args, cwd, env, timeout):
    """Capture a harness run, terminating its isolated process group before repo cleanup."""
    try:
        proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError as error:
        return subprocess.CompletedProcess(args, 127, "", str(error))

    def kill_group():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            if proc.poll() is None:
                raise

    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            kill_group()
            stdout, stderr = proc.communicate()
            return subprocess.CompletedProcess(args, 124, stdout,
                                               stderr + "\nHarness timed out after %s seconds; process group terminated.\n" % timeout)
    finally:
        # Also stop children that outlive a normally exiting CLI, and clean up on interruption.
        kill_group()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def json_events(result):
    events = []
    for line in result.stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            if result.returncode == 0:
                raise
            # A killed process can leave an incomplete final JSON record.
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def run_claude(base, repo, prompt, model=None, tools="Bash,Read,Edit,Write,Glob,Grep,Agent,Skill", plugin=True, extra=None, timeout=900):
    model = model or os.environ.get("BOYSCOUT_CLAUDE_MODEL", "haiku")
    args = ["claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
            "--include-hook-events", "--allowedTools", tools, "--permission-mode", "acceptEdits",
            "--no-session-persistence", "--add-dir", base]
    if plugin:
        args += ["--plugin-dir", ROOT]
    proc = run_process(args, repo, env_for(base, extra), timeout)
    events = json_events(proc)
    text = "\n".join(e.get("result", "") for e in events if e.get("type") == "result")
    hooks = [e for e in events if e.get("type") == "system" and e.get("subtype") == "hook_response"]
    context = "\n".join(str(e.get("output") or e.get("stdout") or "") for e in hooks)
    context = "\n".join(json.loads(line)["hookSpecificOutput"]["additionalContext"]
                        for line in context.splitlines() if line.startswith("{") and "additionalContext" in line)
    return {"events": events, "text": text, "stderr": proc.stderr, "stdout": proc.stdout if proc.returncode else "", "code": proc.returncode, "session": None,
            "transcript": context}


def codex_home(base):
    """A CODEX_HOME inside the test dir, because Codex records every directory it runs in as trusted in its config.
    It shares your login through symlinks and copies your top-level settings, such as the model."""
    home = os.path.join(base, "codex-home")
    if os.path.isdir(home):
        return home
    os.makedirs(home)
    for name in ("auth.json", "auth.lock"):
        if os.path.exists(os.path.join(USER_CODEX, name)):
            os.symlink(os.path.join(USER_CODEX, name), os.path.join(home, name))
    settings = []
    for line in slurp(os.path.join(USER_CODEX, "config.toml")).splitlines():
        if line.startswith("["):
            break
        settings.append(line)
    with open(os.path.join(home, "config.toml"), "w") as handle:
        handle.write("\n".join(settings) + "\n")
    return home


def keep_login(home):
    """If Codex replaced the auth.json link while refreshing the login, move the fresh login back to your home."""
    path = os.path.join(home, "auth.json")
    if os.path.lexists(path) and not os.path.islink(path):
        os.replace(path, os.path.join(USER_CODEX, "auth.json"))


def codex_hooks(repo, env):
    """Install the actual marketplace/plugin in the test CODEX_HOME, then trust its shipped hooks by hash."""
    for args in (["codex", "plugin", "marketplace", "add", ROOT, "--json"],
                 ["codex", "plugin", "add", "boyscout-rule@boyscout-rule", "--json"]):
        subprocess.run(args, cwd=repo, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, check=True, timeout=60)
    listing = list_hooks([], repo, env=env)
    hooks = [h for h in listing["hooks"] if h.get("pluginId") == "boyscout-rule@boyscout-rule"]
    expected = {"sessionStart", "userPromptSubmit", "postToolUse", "sessionEnd"}
    if len(hooks) != 4 or {h["eventName"] for h in hooks} != expected or any(h.get("source") != "plugin" for h in hooks):
        raise RuntimeError("installed plugin did not register its four hooks")
    overrides = ["-c", "hooks.state=" + inline(trust_state(hooks))]
    verified = list_hooks(overrides, repo, env=env)
    approved = [h for h in verified["hooks"] if h.get("pluginId") == "boyscout-rule@boyscout-rule"]
    if len(approved) != 4 or any(h["trustStatus"] != "trusted" or not h["enabled"] for h in approved):
        raise RuntimeError("installed plugin hook trust was not applied")
    return overrides




def run_codex(base, repo, prompt, model=None, plugin=True, extra=None, timeout=900, effort="low"):
    args = ["codex", "exec", "--json", "-c", 'model_reasoning_effort="%s"' % effort, "-s", "workspace-write",
            "-c", "allow_login_shell=false"]
    model = model or os.environ.get("BOYSCOUT_CODEX_MODEL")
    if model:
        args += ["-m", model]
    home = codex_home(base)
    env = env_for(base, dict(extra or {}, CODEX_HOME=home))
    if plugin:
        args += codex_hooks(repo, env)
    args += ["--add-dir", base, "--add-dir", os.path.join(repo, ".git"), prompt]
    try:
        proc = run_process(args, repo, env, timeout)
    finally:
        keep_login(home)
    events = json_events(proc)
    texts = [e["item"]["text"] for e in events if e.get("type") == "item.completed" and e["item"].get("type") == "agent_message"]
    session = next((e.get("thread_id") for e in events if e.get("thread_id")), None)
    return {"events": events, "text": "\n".join(texts), "stderr": proc.stderr, "stdout": proc.stdout if proc.returncode else "", "code": proc.returncode, "session": session,
            "transcript": codex_transcript(home, session)}


def find_file(root, suffix):
    for folder, _, names in os.walk(root):
        for name in names:
            if name.endswith(suffix):
                return os.path.join(folder, name)
    return None


def slurp(path):
    if not path:
        return ""
    with open(path, errors="replace") as handle:
        return handle.read()


def claude_transcript(session):
    return slurp(find_file(os.path.expanduser("~/.claude/projects"), "%s.jsonl" % session)) if session else ""


def codex_transcript(home, session):
    return slurp(find_file(os.path.join(home, "sessions"), "%s.jsonl" % session)) if session else ""


def forget(harness, session):
    """Remove the test session from the user's history; Codex sessions stay in the test's own CODEX_HOME."""
    if session and harness == "claude":
        path = find_file(os.path.expanduser("~/.claude/projects"), "%s.jsonl" % session)
        if path and "/boyscout-" in slurp(path)[:20000]:
            os.remove(path)


def read(base, name):
    try:
        with open(os.path.join(base, name)) as handle:
            return handle.read()
    except OSError:
        return ""


def run_opencode(base, repo, prompt, model=None, plugin=True, extra=None, timeout=900):
    """Private server, config, data and cache; never install into the user's OpenCode."""
    env = env_for(base, extra)
    for name, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        env[name] = os.path.join(base, folder)
    for name in list(env):
        if name.startswith("OPENCODE_"):
            env.pop(name)
    env["HOME"] = os.path.join(base, "home")
    env["PWD"] = repo
    os.makedirs(env["HOME"], exist_ok=True)
    config_dir = os.path.join(env["XDG_CONFIG_HOME"], "opencode")
    os.makedirs(config_dir, exist_ok=True)
    context_log = os.path.join(base, "context.jsonl")
    env["BOYSCOUT_PROBE_LOG"] = context_log
    config = {"plugins": [os.path.join(ROOT, "opencode"), os.path.join(ROOT, "tests", "probe", "opencode")]} if plugin else {}
    with open(os.path.join(config_dir, "opencode.json"), "w") as handle:
        json.dump(config, handle)
    args = ["opencode", "run", "--standalone", "--auto", "--format", "json", "--model",
            model or os.environ.get("BOYSCOUT_OPENCODE_MODEL", "opencode/big-pickle"), prompt]
    proc = run_process(args, repo, env, timeout)
    events = json_events(proc)
    texts = [e["part"]["text"] for e in events if e.get("type") == "text"]
    session = next((e["sessionID"] for e in events if e.get("sessionID")), None)
    # OpenCode may report a provider error in JSON while still exiting zero.
    errors = [e for e in events if e.get("type") == "error"]
    return {"events": events, "text": "\n".join(texts), "stderr": proc.stderr + (json.dumps(errors) if errors else ""),
            "code": proc.returncode or (1 if errors else 0), "stdout": proc.stdout if proc.returncode else "",
            "session": session, "transcript": read(base, "context.jsonl")}


RUNNERS = dict(claude=run_claude, codex=run_codex, opencode=run_opencode)

import bisect
import hashlib
import json
import os
import re
import shlex
import sys
import time

try:
    import fcntl
except ImportError:
    fcntl = None

MAX_NUDGES = 3
FIX_WINDOW = 5
PROMPT_GAP = 5
STALE_SECONDS = 86400
TRANSCRIPT_READS = (262144, 4194304)
MAX_COMMAND = 20000
LOCK_SECONDS = 0.05
MAX_PROMPT = 100000

IGNORED_PROGRAMS = {
    "ls", "cat", "head", "tail", "grep", "egrep", "rg", "ag", "find", "fd", "echo", "printf", "test", "[",
    "which", "type", "true", "false", "sed", "awk", "wc", "sort", "uniq", "jq", "less", "more",
    "file", "stat", "diff", "pwd", "git", "gh", "sleep", "date", "tree", "xargs", "tee", "cut", "tr",
    "mkdir", "rm", "cp", "mv", "touch", "chmod", "readlink", "realpath", "dirname", "basename", "nl",
}
PREFIXES = {"sudo", "time", "nice", "nohup", "exec", "command", "env", "timeout"}
PREFIX_ARGS = {"sudo": {"-u", "-g", "-C", "-h", "--user", "--group"}, "nice": {"-n"}, "env": {"-u", "-C", "--unset", "--chdir"},
               "timeout": {"-k", "--kill-after", "-s", "--signal"}}
NO_MATCH = {"rg", "grep", "egrep", "ag", "test", "[", "diff", "cmp"}
SETUP = {"cd", "export", "source", ".", "set", "unset", "pushd", "popd", "alias"}
OPERATORS = {"&&", "||", ";", "|", "&", "|&", ";;", "(", ")"}
SHELLS = {"sh", "bash", "zsh", "dash"}
HEREDOC = re.compile(r"<<-?\s*['\"]?[A-Za-z_]")
CONTROL = {"for", "while", "until", "if", "case", "select", "function", "{", "}", "do", "done", "then", "else", "elif", "fi", "esac"}
EXIT_HEADER = re.compile(r"\AExit code [1-9]\d*(?:\r?\n|\Z)")
SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,39}$")
FAILURE_TEXT = re.compile(
    r"^[^\S\r\n]*(?:npm (?:error|ERR!)|error:|fatal:|E:|FAIL(?:ED)?\b|make(?:\[\d+\])?: \*\*\*|traceback \(most recent call last\))"
    r"|command not found|no such file or directory|missing script|unknown (?:command|option|script|subcommand)"
    r"|no rule to make target|cannot find module|is not recognized as|can't open file|unrecognized arguments|invalid choice"
    r"|(?<![\d.])[1-9]\d* (?:failed|failing|errors?)\b"
    r"|\bexit(?:[ _]?code|[ _]?status)?\s*[=:]\s*[1-9]\d*\b",
    re.IGNORECASE | re.MULTILINE,
)
SETUP_WORD = re.compile(r"\b(?:cd|pushd|source)\b", re.IGNORECASE)
SETUP_ERROR = re.compile(r"no such file|can'?t cd|not a directory|permission denied", re.IGNORECASE)
WRONG_FORM = re.compile(
    r"/[^\s/]*: command not found|command not found: [^\s/]*/"
    r"|missing script|unknown (?:command|option|script|subcommand)|no rule to make target|cannot find module"
    r"|is not recognized as|can't open file|unrecognized arguments|invalid choice"
    r"|^(?:\(eval\)|(?:\S*/)?(?:ba|z|da|k)?sh\b)[^\n]*(?:no such file|/[^\s/]*: not found)",
    re.IGNORECASE | re.MULTILINE,
)
OPEN_TAG = re.compile(r"<([a-z][a-z-]*)[^<>]{0,500}>", re.IGNORECASE)
CLOSE_TAG = re.compile(r"</([a-z][a-z-]*)>", re.IGNORECASE)
SYSTEM_PROMPT = re.compile(
    r"^\s*<(?:agent-message|task-notification|system-reminder|command-|local-command|bash-|user-prompt-submit-hook)")
CORRECTION = re.compile(
    r"\bi\s+(?:already\s+)?(?:told|asked)\s+you\b"
    r"|(?:^|[.!?]\s+)i\s+(?:already\s+)?said\b"
    r"|\bthat'?s\s+(?:wrong|not\s+right|incorrect|not\s+how)\b"
    r"|\bis\s+(?:wrong|incorrect)\b"
    r"|\bwrong\s+(?:command|file|path|branch|directory|folder|way|script)\b"
    r"|(?:^|[.!?]\s+)again\s*[?!]"
    r"|\b(?:same|this)\s+(?:mistake|error)\s+again\b"
    r"|\bnot\s+like\s+that\b"
    r"|\bstop\s+(?:doing|using|running)\b"
    r"|\bwhy\s+did\s+you\b",
    re.IGNORECASE | re.MULTILINE,
)
DIRECTION = re.compile(
    r"\b(?:we|i)\s+(?:don['\u2019]?t|never|no\s+longer)\s+(?:use|support)\b|\bis\s+(?:outdated|stale)\b", re.IGNORECASE)

CONTEXT = (
    "boyscout-rule is active. When you verify an incidental repo instruction, doc, small defect or recurring "
    "procedure worth fixing, invoke the boyscout-rule skill before deciding how to handle it, including during "
    "check or explain tasks, and never apply that fix in the user's checkout. Installing "
    "the plugin is the user's standing request to make qualifying fixes in a separate worktree, push "
    "`boyscout/*` branches to this repo's origin and open draft PRs. For those actions only, it is the request "
    "that rules such as \"push only when asked\" or \"only make changes I ask for\" wait for, so such rules are "
    "no reason to skip. Skip only for an explicit prohibition or approval requirement in the session or in "
    "standing user instructions, such as \"no changes\", \"never push\", \"no PRs\" or \"ask before every push\", "
    "whether or not it names boyscout; then report the finding and leave its files untouched. "
    "Session id for branch names: {sid}."
)
NUDGE_FAILURE = (
    "boyscout-rule: {program} failed and a different {program} command then ran. If a repo doc, "
    "script or instruction pointed you to the failing form, fix that source with the boyscout-rule skill."
)
NUDGE_CORRECTION = (
    "boyscout-rule: the user corrected you. If a repo instruction, doc or skill caused the mistake, fix it "
    "with the boyscout-rule skill; if it was a personal preference, do nothing."
)
CLAUDE_MEMORY_ON = "boyscout-rule: Claude Code auto memory is on. Run /memory and turn it off."
CODEX_MEMORY_ON = "boyscout-rule: Codex memories are on. Set features.memories = false in your Codex config.toml."


def digest(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def state_dir():
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.environ.get("PLUGIN_DATA")
    if not base:
        cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
        base = os.path.join(cache, "boyscout-rule")
    return os.path.join(base, "sessions")


def state_path(session_id):
    return os.path.join(state_dir(), hashlib.sha256(session_id.encode()).hexdigest()[:32] + ".json")


class Busy(Exception):
    pass


class State:
    """Session counters under an exclusive, non-blocking lock; written atomically."""

    def __init__(self, session_id):
        self.path = state_path(session_id)
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        self.lock = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + LOCK_SECONDS
        while True:
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(self.lock)
                    raise Busy()
                time.sleep(0.01)
        try:
            with open(self.path) as handle:
                self.data = json.load(handle)
        except (OSError, ValueError):
            self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}

    def save(self):
        temp = "%s.%d.tmp" % (self.path, os.getpid())
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(self.data))
        os.replace(temp, self.path)

    def close(self):
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        os.close(self.lock)


def nudge_allowed(data):
    return data.get("nudges", 0) < MAX_NUDGES


def spend(data):
    data["nudges"] = data.get("nudges", 0) + 1


def lex(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return command.split()


def join_continuations(command):
    if "\\\n" not in command:
        return command
    out, quote, i = [], None, 0
    while i < len(command):
        char = command[i]
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == "\\":
            if command[i + 1:i + 2] != "\n":
                out.append(command[i:i + 2])
            i += 2
            continue
        elif char in "'\"":
            quote = None if quote == char else quote or char
        out.append(char)
        i += 1
    return "".join(out)


def segments(command, sequential=False):
    """Simple commands of a shell line, as word lists, split on operators outside quotes.

    With sequential=True, stop at the first `||`: later commands may not have run.
    """
    command = join_continuations(command)
    if HEREDOC.search(command):
        command = command.split("\n", 1)[0]
    parts, current, skip = [], [], False
    for token in lex(command.replace("\n", " ; ")):
        if skip:
            skip = False
        elif token and set(token) <= set("<>&") and ("<" in token or ">" in token):
            if current and current[-1].isdigit():
                current.pop()
            skip = True
        elif token in OPERATORS or (token and set(token) <= set("&|;()")):
            if current:
                parts.append(current)
            current = []
            if sequential and token == "||":
                return parts
        else:
            current.append(token)
    if current:
        parts.append(current)
    return parts


def strip(words):
    """Drop env assignments, wrappers and redirections; keep the assignments for the fingerprint."""
    assignments, rest = [], list(words)
    while rest:
        word = rest[0]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            assignments.append(rest.pop(0))
        elif word in PREFIXES:
            rest.pop(0)
            takes = PREFIX_ARGS.get(word, set())
            while rest and rest[0].startswith("-"):
                flag = rest.pop(0)
                if flag in takes and rest:
                    rest.pop(0)
            if word == "timeout" and rest and re.match(r"^\d", rest[0]):
                rest.pop(0)
        else:
            break
    return assignments, rest


def name_of(words):
    return os.path.basename(words[0]) if words else ""


def commands(command):
    """(program, fingerprint) for each meaningful simple command that ran in order; shell control structures are skipped."""
    found = []
    for raw in segments(command, sequential=True):
        if raw[0] in CONTROL:
            continue
        assignments, words = strip(raw)
        program = name_of(words)
        if not program or program.startswith("-") or program in SETUP or program in IGNORED_PROGRAMS:
            continue
        found.append((program, digest("\0".join(assignments + [w for w in words if not w.startswith("-")]))))
    return found


def masked(command, main):
    parts = segments(command)
    return bool(parts) and strip(parts[-1])[1] != main


def read_tail(path, size):
    with open(path, "rb") as transcript:
        transcript.seek(0, 2)
        end = transcript.tell()
        transcript.seek(max(0, end - size))
        return transcript.read(), end <= size


def codex_exit_code(payload):
    path = payload.get("transcript_path")
    call_id = payload.get("tool_use_id") or ""
    if not path or not call_id:
        return None
    needle = call_id.encode()
    for size in TRANSCRIPT_READS:
        try:
            chunk, whole = read_tail(path, size)
        except OSError:
            return None
        for line in reversed(chunk.splitlines()):
            if needle not in line or b'"exit_code"' not in line:
                continue
            try:
                item = json.loads(line).get("payload", {}).get("item", {})
            except ValueError:
                continue
            if item.get("id") == call_id and isinstance(item.get("exit_code"), int):
                return item["exit_code"]
        if whole:
            return None
    return None


def output_text(payload):
    response = payload.get("tool_response")
    if isinstance(response, dict):
        response = "\n".join(str(response.get(k) or "") for k in ("stdout", "stderr"))
    if not isinstance(response, str):
        response = payload.get("error") if isinstance(payload.get("error"), str) else ""
    return re.sub(r"^Exit code \d+\n?", "", response)


def setup_error(output):
    for line in output.splitlines():
        word = SETUP_WORD.search(line)
        if word and SETUP_ERROR.search(line, word.end()):
            return True
    return False


def setup_failed(command, main, output):
    if setup_error(output):
        return True
    before = []
    for raw in segments(command):
        words = strip(raw)[1]
        if words == main:
            break
        before.append(name_of(words))
    return bool({"source", "."} & set(before)) and "no such file" in output.lower()


def guarded(command, main, output):
    """A check before the program (test, grep, ...) may have stopped it; only its own error counts."""
    for raw in segments(command):
        words = strip(raw)[1]
        if words == main:
            return False
        if name_of(words) in NO_MATCH:
            return not re.search(r"(?<![\w.-])%s(?!\w)" % re.escape(name_of(main)), output)
    return False


def outcome(harness, event, payload, command, main):
    """True for success, False for failure, None when unknown."""
    output = output_text(payload)[-8000:]
    if harness == "opencode":
        code = payload.get("exit_code")
        if payload.get("interrupted") or type(code) is not int:
            return None
        ok = code == 0
    elif harness == "claude":
        response = payload.get("tool_response")
        if payload.get("is_interrupt") or (isinstance(response, dict) and response.get("interrupted")):
            return None
        if (payload.get("tool_input") or {}).get("run_in_background") or (
                isinstance(response, dict) and response.get("backgroundTaskId")):
            return None
        ok = event != "PostToolUseFailure"
    else:
        code = codex_exit_code(payload)
        if code is None:
            if setup_failed(command, main, output):
                return None
            if WRONG_FORM.search(output):
                return False
            return None if FAILURE_TEXT.search(output) else True
        ok = code == 0
    if not ok and (setup_failed(command, main, output) or guarded(command, main, output)):
        return None
    if masked(command, main):
        if FAILURE_TEXT.search(output) or WRONG_FORM.search(output):
            return False
        last = name_of(strip(segments(command)[-1])[1])
        return True if ok or last in NO_MATCH else None
    return ok


def on_shell(harness, event, payload, data):
    command = (payload.get("tool_input") or {}).get("command")
    if isinstance(command, list):
        parts = [str(part) for part in command]
        if len(parts) >= 3 and os.path.basename(parts[0]) in SHELLS and parts[-2].startswith("-") and "c" in parts[-2]:
            command = parts[-1]
        else:
            command = " ".join(shlex.quote(part) for part in parts)
    if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND:
        return None
    found = commands(command)
    if not found:
        return None
    program, fingerprint = found[0]
    main = strip(next(raw for raw in segments(command) if name_of(strip(raw)[1]) == program))[1]
    if harness == "claude" and event == "PostToolUseFailure" and not EXIT_HEADER.match(str(payload.get("error") or "")):
        return None
    data["calls"] = data.get("calls", 0) + 1
    result = outcome(harness, event, payload, command, main)
    if result is None or (result is False and len(found) > 1):
        return None
    wrong = result is False and bool(WRONG_FORM.search(output_text(payload)[-8000:]))
    return failure_nudge(data, program, fingerprint, result, wrong)


def failure_nudge(data, program, fingerprint, ok, wrong=False):
    """Nudge when, after a wrong-command error, a different command of the same program works or runs at all."""
    failures = data.setdefault("failures", {})
    key = digest(program)
    calls = data["calls"]
    recent = [f for f in failures.get(key, []) if calls - f["at"] <= FIX_WINDOW and f.get("wrong", True)]
    if wrong:
        failures[key] = (recent + [{"hash": fingerprint, "at": calls}])[-FIX_WINDOW:]
        return None
    if not ok and not any(f["hash"] != fingerprint for f in recent):
        return None
    failures.pop(key, None)
    nudged = data.setdefault("nudged_programs", [])
    if key in nudged or not nudge_allowed(data):
        return None
    if not any(f["hash"] != fingerprint for f in recent):
        return None
    nudged.append(key)
    spend(data)
    return NUDGE_FAILURE.format(program=program if SAFE_LABEL.match(program) else "a command")


def strip_tags(text):
    """Remove each <tag ...>...</tag> block in one pass; unclosed tags stay."""
    closes = {}
    for match in CLOSE_TAG.finditer(text):
        closes.setdefault(match.group(1).lower(), []).append(match.start())
    kept, pos = [], 0
    while True:
        match = OPEN_TAG.search(text, pos)
        if not match:
            break
        starts = closes.get(match.group(1).lower(), [])
        index = bisect.bisect_left(starts, match.end())
        if index == len(starts):
            kept.append(text[pos:match.end()])
            pos = match.end()
            continue
        kept.append(text[pos:match.start()] + " ")
        pos = starts[index] + len(match.group(1)) + 3
    kept.append(text[pos:])
    return "".join(kept)


def human_text(prompt):
    if not isinstance(prompt, str):
        return ""
    text = strip_tags(prompt[:MAX_PROMPT]).strip()
    if not text or SYSTEM_PROMPT.match(text) or text.startswith("/") or text.startswith("$"):
        return ""
    text = re.sub(r"```.*?(```|$)", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", "CODE", text)
    lines = [line for line in text.splitlines() if not line.lstrip().startswith(">")]
    return "\n".join(lines)[:600]


def on_prompt(payload, data):
    message_id = payload.get("message_id")
    if message_id:
        fingerprint = digest(str(message_id))
        if data.get("last_prompt_id") == fingerprint:
            return None
        data["last_prompt_id"] = fingerprint
    text = human_text(payload.get("prompt"))
    if not text:
        return None
    data["prompts"] = data.get("prompts", 0) + 1
    last = data.get("last_correction")
    if not CORRECTION.search(text) and not (data["prompts"] > 1 and DIRECTION.search(text)):
        return None
    if last is not None and data["prompts"] - last < PROMPT_GAP:
        return None
    if not nudge_allowed(data):
        return None
    data["last_correction"] = data["prompts"]
    spend(data)
    return NUDGE_CORRECTION


def read_json(path):
    try:
        with open(path) as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def claude_memory_on(project):
    if truthy(os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "")):
        return False
    home = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    files = [
        "/Library/Application Support/ClaudeCode/managed-settings.json",
        "/etc/claude-code/managed-settings.json",
    ]
    if project:
        files += [os.path.join(project, ".claude", "settings.local.json"), os.path.join(project, ".claude", "settings.json")]
    files.append(os.path.join(home, "settings.json"))
    for path in files:
        settings = read_json(path)
        env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
        if truthy(env.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "")):
            return False
        if isinstance(settings.get("autoMemoryEnabled"), bool):
            return settings["autoMemoryEnabled"]
    return True


def codex_memory_on():
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    try:
        with open(os.path.join(home, "config.toml")) as handle:
            text = handle.read()
    except OSError:
        return False
    section = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("["):
            section = line.strip("[] ")
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().lower()
        if (section == "features" and key == "memories") or (section == "" and key == "features.memories"):
            return value == "true"
    return False


def remove_state(path):
    try:
        os.remove(path)
    except OSError:
        pass
    if fcntl is None:
        return
    try:
        fd = os.open(path + ".lock", os.O_RDWR)
    except OSError:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.remove(path + ".lock")
    except OSError:
        pass
    finally:
        os.close(fd)


def clean_stale():
    folder = state_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        return
    cutoff = time.time() - STALE_SECONDS
    for name in names:
        path = os.path.join(folder, name)
        try:
            if os.path.getmtime(path) >= cutoff:
                continue
            if name.endswith(".lock"):
                if fcntl is None or os.path.exists(path[:-5]):
                    continue
                fd = os.open(path, os.O_RDWR)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    os.remove(path)
                finally:
                    os.close(fd)
            else:
                os.remove(path)
        except OSError:
            pass


def short_id(session_id):
    return hashlib.sha256(session_id.encode()).hexdigest()[:10] if session_id else "none"


def on_start(harness, payload):
    clean_stale()
    sid = short_id(str(payload.get("session_id") or ""))
    output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": CONTEXT.format(sid=sid)}}
    if harness == "opencode" and payload.get("correction_context"):
        output["hookSpecificOutput"]["additionalContext"] += "\n" + NUDGE_CORRECTION
    if payload.get("source", "startup") == "startup":
        if harness == "claude" and claude_memory_on(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")):
            output["systemMessage"] = CLAUDE_MEMORY_ON
        elif harness == "codex" and codex_memory_on():
            output["systemMessage"] = CODEX_MEMORY_ON
    return output


def handle(harness, event, payload):
    if event == "SessionStart":
        return on_start(harness, payload)
    session_id = payload.get("session_id")
    if not session_id:
        return None
    if event == "SessionEnd":
        remove_state(state_path(str(session_id)))
        return None
    if payload.get("agent_id") or fcntl is None:
        return None
    if event in ("PostToolUse", "PostToolUseFailure") and payload.get("tool_name") not in ("Bash", "shell", "exec_command"):
        return None
    try:
        state = State(str(session_id))
    except Busy:
        return None
    try:
        call_id = payload.get("call_id")
        if call_id:
            fingerprint = digest(str(call_id))
            if state.data.get("last_call_id") == fingerprint:
                return None
            state.data["last_call_id"] = fingerprint
        if event == "UserPromptSubmit":
            message = on_prompt(payload, state.data)
        elif event in ("PostToolUse", "PostToolUseFailure"):
            message = on_shell(harness, event, payload, state.data)
        else:
            message = None
        state.save()
    finally:
        state.close()
    if not message:
        return None
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}


def main(argv):
    if len(argv) < 3 or "CI" in os.environ:
        return
    harness, event = argv[1], argv[2]
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    output = handle(harness, event, payload)
    if output:
        sys.stdout.write(json.dumps(output))


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception:
        pass
    sys.exit(0)

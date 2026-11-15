import ast
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = re.compile(r"(ghp_[A-Za-z0-9]{20,}|github_pat_|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY)")
PERSONAL = re.compile(r"/Users/[a-z]|/home/(?!user\b)[a-z]+/|claude-501")
STDLIB = {"bisect", "fcntl", "hashlib", "json", "os", "re", "shlex", "sys", "time"}
failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else ": " + detail))
    if not ok:
        failures.append(name)


def tracked():
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True).stdout
    return [f for f in out.splitlines() if os.path.isfile(os.path.join(ROOT, f))]


def skill_frontmatter():
    with open(os.path.join(ROOT, "skills", "boyscout-rule", "SKILL.md")) as handle:
        text = handle.read()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return False, "no frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    if set(fields) != {"name", "description"}:
        return False, "fields %s" % sorted(fields)
    if fields["name"] != "boyscout-rule" or not re.match(r"^[a-z0-9-]{1,64}$", fields["name"]):
        return False, "bad name"
    if not 0 < len(fields["description"]) <= 1024 or "<" in fields["description"] or ">" in fields["description"]:
        return False, "bad description"
    return True, ""


def hook_configs():
    problems = []
    for name in ("hooks.json", "codex.json"):
        with open(os.path.join(ROOT, "hooks", name)) as handle:
            config = json.load(handle)["hooks"]
        for event, groups in config.items():
            for group in groups:
                for hook in group["hooks"]:
                    command = hook.get("command", "")
                    if "${CLAUDE_PLUGIN_ROOT}/hooks/boyscout.py" not in command or "python3 -I" not in command:
                        problems.append("%s %s command" % (name, event))
                    if not command.endswith("|| true") or not isinstance(hook.get("timeout"), int):
                        problems.append("%s %s guard/timeout" % (name, event))
    with open(os.path.join(ROOT, "hooks", "boyscout.py")) as handle:
        source = handle.read()
    if re.search(r"exit\(\s*2\s*\)|\"decision\"|'decision'|\"block\"", source):
        problems.append("blocking output in boyscout.py")
    return problems


def imports():
    with open(os.path.join(ROOT, "hooks", "boyscout.py")) as handle:
        tree = ast.parse(handle.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    return names - STDLIB


def main():
    ok, detail = skill_frontmatter()
    check("S3 skill frontmatter", ok, detail)
    for python in ("/usr/bin/python3", sys.executable):
        if os.path.exists(python):
            result = subprocess.run([python, "-c", "import ast,sys; ast.parse(open(sys.argv[1]).read(), feature_version=(3, 9))",
                                     os.path.join(ROOT, "hooks", "boyscout.py")], capture_output=True, text=True)
            check("S4 compiles on " + python, result.returncode == 0, result.stderr[-300:])
    extra = imports()
    check("S5 standard library only", not extra, ", ".join(sorted(extra)))
    leaks = []
    for path in tracked():
        if path.startswith("tests/static_checks.py"):
            continue
        try:
            with open(os.path.join(ROOT, path), errors="ignore") as handle:
                text = handle.read()
        except OSError:
            continue
        if SECRET.search(text) or PERSONAL.search(text):
            leaks.append(path)
    check("S6 no secrets or personal paths", not leaks, ", ".join(leaks))
    problems = hook_configs()
    check("S7 hook configs", not problems, "; ".join(problems))
    for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        with open(os.path.join(ROOT, manifest)) as handle:
            data = json.load(handle)
        check("manifest " + manifest, data.get("name") == "boyscout-rule" and data.get("version"), json.dumps(data)[:200])
    with open(os.path.join(ROOT, ".codex-plugin/plugin.json")) as handle:
        codex = json.load(handle)
    skills = os.path.join(ROOT, codex.get("skills", ""), "boyscout-rule", "SKILL.md")
    hooks = os.path.join(ROOT, codex.get("hooks", ""))
    check("codex manifest paths", os.path.isfile(skills) and os.path.isfile(hooks) and os.path.realpath(hooks) == os.path.join(ROOT, "hooks", "codex.json"),
          "%s %s" % (codex.get("skills"), codex.get("hooks")))
    with open(os.path.join(ROOT, ".claude-plugin/plugin.json")) as a, open(os.path.join(ROOT, ".codex-plugin/plugin.json")) as b:
        check("manifest versions match", json.load(a)["version"] == json.load(b)["version"])
    with open(os.path.join(ROOT, "package.json")) as handle:
        package = json.load(handle)
    with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as handle:
        check("OpenCode package version matches", package["version"] == json.load(handle)["version"])
    check("OpenCode has no runtime dependencies", not package.get("dependencies"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

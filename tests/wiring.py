"""Phase 3: the hooks fire in the real harness and their text reaches the model."""
import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from harness import ROOT, RUNNERS, forget, make_repo, require_local  # noqa: E402

ECHO = " Do not create branches, commits or pull requests. In your final response quote every boyscout-rule reminder you received verbatim, including the active context."
REPO = {
    "README.md": "# Demo\n\nRun the tests with `npm run tests`.\n",
    "package.json": json.dumps({"name": "demo", "version": "1.0.0", "scripts": {"test": "echo all tests pass", "lint": "exit 1"}}, indent=2) + "\n",
    "Makefile": "a:\n\t@echo step a\nb:\n\t@echo step b\n",
    "build.js": "console.log('built')\n",
    "check.js": "process.exit(3)\n",
}
CASES = {
    "W2+W3": ("Follow the README to run the tests. If the documented command fails, find the right command and run it.",
              ["boyscout-rule is active", "npm failed"]),
    "W4": ("no, we use pnpm here, not npm. That is all; nothing to run.", ["boyscout-rule: the user corrected you"]),
    "W5": ("Run these shell commands, each as its own separate tool call, in this order: `make a`, `make b`, "
           "`node build.js`, `make a`, `make b`, `node build.js`.", ["the same sequence"]),
    "W7": ("Run `npm run tests` as its own tool call, then run `npm run lint` as a separate tool call. Both are expected "
           "to fail; do not try anything else.", ["npm failed"]),
    "W6": ("Run `node check.js` as its own tool call; it exits with code 3 and prints nothing. Then run `node build.js` "
           "as a separate tool call.", ["node failed"]),
}


def run(harness, name):
    prompt, expected = CASES[name]
    base, repo = make_repo(REPO)
    try:
        runner = RUNNERS[harness]
        result = runner(base, repo, prompt + ECHO)
        os.makedirs(os.path.join(ROOT, "tests", "out"), exist_ok=True)
        with open(os.path.join(ROOT, "tests", "out", "wiring-%s-%s.json" % (harness, name)), "w") as handle:
            json.dump(result, handle, indent=1)
        missing = ["context: " + e for e in expected if e not in result["transcript"]]
        missing += ["model: " + e for e in expected if e not in result["text"]]
        forget(harness, result["session"])
        return harness, name, not missing and result["code"] == 0 and bool(result["text"].strip()), missing, result["code"]
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main():
    require_local()
    harnesses = sys.argv[1:] or list(RUNNERS)
    if any(h not in RUNNERS for h in harnesses):
        raise SystemExit("unknown harness; choose claude, codex or opencode")
    jobs = [(h, n) for h in harnesses for n in CASES]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda job: run(*job), jobs))
    for harness, name, ok, missing, code in results:
        print("%s %-6s %-6s exit=%s %s" % ("PASS" if ok else "FAIL", harness, name, code, "" if ok else "missing: %s" % missing))
    return 0 if all(r[2] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

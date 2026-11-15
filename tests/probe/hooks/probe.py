import json
import os
import sys
import time

START = time.time()
EVENT = sys.argv[1] if len(sys.argv) > 1 else "unknown"
HARNESS = sys.argv[2] if len(sys.argv) > 2 else "unknown"
ENV_KEYS = (
    "PATH", "PWD", "HOME",
    "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_ENTRYPOINT",
    "PLUGIN_ROOT", "PLUGIN_DATA", "CODEX_HOME", "CODEX_THREAD_ID", "CODEX_SANDBOX",
)

raw = sys.stdin.read()
try:
    payload = json.loads(raw) if raw.strip() else None
except ValueError:
    payload = {"_unparsed": raw[:2000]}

record = {
    "event": EVENT,
    "harness": HARNESS,
    "python": sys.version.split()[0],
    "executable": sys.executable,
    "cwd": os.getcwd(),
    "env": {k: os.environ.get(k) for k in ENV_KEYS if k in os.environ},
    "env_keys": sorted(k for k in os.environ if any(t in k for t in ("CLAUDE", "CODEX", "PLUGIN", "OPENCODE"))),
    "input": payload,
}
log = os.environ.get("BOYSCOUT_PROBE_LOG") or os.path.join(os.path.expanduser("~"), ".boyscout-probe.jsonl")

word = {"SessionStart": "PROBE-SS-4417", "UserPromptSubmit": "PROBE-UPS-9023", "PostToolUse": "PROBE-PTU-3361",
        "PostToolUseFailure": "PROBE-PTUF-5578"}.get(EVENT)
out = None
if word:
    out = {"hookSpecificOutput": {"hookEventName": EVENT, "additionalContext": "Probe code word: " + word}}
    sys.stdout.write(json.dumps(out))

if HARNESS == "codex" and EVENT == "PostToolUse" and isinstance(payload, dict) and payload.get("transcript_path"):
    found = None
    with open(payload["transcript_path"], "rb") as transcript:
        transcript.seek(0, 2)
        transcript.seek(max(0, transcript.tell() - 262144))
        for line in transcript.read().splitlines():
            if payload.get("tool_use_id", "").encode() in line and b'"exit_code"' in line:
                try:
                    item = json.loads(line).get("payload", {}).get("item", {})
                except ValueError:
                    continue
                if item.get("id") == payload.get("tool_use_id"):
                    found = item.get("exit_code")
    record["transcript_exit_code"] = found

record["output"] = out
record["elapsed_ms"] = round((time.time() - START) * 1000, 1)
with open(log, "a") as handle:
    handle.write(json.dumps(record) + "\n")

"""Register hooks for one Codex run through -c overrides, trusted by hash, without touching ~/.codex.
Pass env with the run's CODEX_HOME so the app-server lists the hooks the run will see."""
import json
import os
import signal
import subprocess
import sys
import threading


def inline(value):
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + inline(v) for k, v in value.items() if v is not None) + "}"
    if isinstance(value, list):
        return "[" + ",".join(inline(v) for v in value) + "]"
    return json.dumps(value)


def hook_table(commands, timeout=10):
    """commands: {EventName: command or (matcher, command)}."""
    table = {}
    for event, spec in commands.items():
        matcher, command = spec if isinstance(spec, tuple) else (None, spec)
        group = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
        if matcher:
            group["matcher"] = matcher
        table[event] = [group]
    return table


def app_server(overrides, cwd, method, params, codex="codex", timeout=30, env=None):
    proc = subprocess.Popen([codex, "--no-daemon"] + overrides + ["app-server", "--listen", "stdio://"],
                            cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)

    def kill_group():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # macOS may report EPERM for an already dead process group.
            if proc.poll() is None:
                raise

    timer = threading.Timer(timeout, kill_group)
    timer.start()

    def send(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "boyscout-tests", "version": "0"}, "capabilities": {"experimentalApi": True}}})
        for line in proc.stdout:
            message = json.loads(line)
            if message.get("error"):
                raise RuntimeError(json.dumps(message["error"]))
            if message.get("id") == 1:
                send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                send({"jsonrpc": "2.0", "id": 2, "method": method, "params": params})
            elif message.get("id") == 2:
                return message["result"]
        raise RuntimeError("app-server exited: " + proc.stderr.read()[-2000:])
    finally:
        timer.cancel()
        timer.join()
        kill_group()
        proc.wait()
        kill_group()


def list_hooks(overrides, cwd, codex="codex", env=None):
    result = app_server(overrides, cwd, "hooks/list", {"cwds": [cwd]}, codex, env=env)
    return result["data"][0]


def trust_state(hooks):
    """Installed plugins expose declaration and bundle fingerprints separated by '/'."""
    state = {}
    for hook in hooks:
        declaration, separator, content = hook["currentHash"].partition("/")
        state[hook["key"]] = {"trusted_hash": declaration}
        if separator:
            state[hook["key"]]["trusted_content_hash"] = content
    return state


def trusted_overrides(commands, cwd, marker, codex="codex", timeout=10, env=None):
    table = hook_table(commands, timeout)
    base = ["-c", "hooks=" + inline(table)]
    listing = list_hooks(base, cwd, codex, env)
    state = trust_state([h for h in listing["hooks"] if marker in (h.get("command") or "")])
    table["state"] = state
    return ["-c", "hooks=" + inline(table)], listing


if __name__ == "__main__":
    spec = json.loads(sys.argv[1])
    overrides, listing = trusted_overrides(spec["commands"], spec["cwd"], spec["marker"])
    print(json.dumps({"overrides": overrides, "listing": listing}))

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests", "lib"))
import harness  # noqa: E402
from codex_hooks import trust_state  # noqa: E402
from harness import env_for, json_events, run_process  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wiring = load("wiring", "tests/wiring.py")
behavior = load("behavior", "tests/behavior.py")


class HarnessTest(unittest.TestCase):
    def test_plugin_trust_requires_both_fingerprints(self):
        hooks = [{"key": "plugin", "currentHash": "sha256:command/sha256:bundle"},
                 {"key": "local", "currentHash": "sha256:local"}]
        self.assertEqual(trust_state(hooks), {
            "plugin": {"trusted_hash": "sha256:command", "trusted_content_hash": "sha256:bundle"},
            "local": {"trusted_hash": "sha256:local"},
        })

    def test_only_test_children_lose_ci_and_github_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {"CI": "true", "GH_TOKEN": "private", "GITHUB_TOKEN": "private", "GH_REPO": "wrong/repo"}):
                env = env_for(folder)
                self.assertEqual(os.environ["CI"], "true")
                for name in ("CI", "GH_TOKEN", "GITHUB_TOKEN", "GH_REPO"):
                    self.assertNotIn(name, env)
                self.assertTrue(env["PATH"].startswith(os.path.join(folder, "bin")))

    def test_wiring_requires_context_model_echo_and_a_successful_exit(self):
        expected = wiring.CASES["W4"][1][0]
        good = {"transcript": expected, "text": expected, "code": 0, "session": None}
        for changes, passes in (({}, True), ({"code": 1}, False), ({"text": ""}, False),
                                ({"text": "context never reached me"}, False), ({"transcript": ""}, False)):
            with self.subTest(changes=changes):
                result = dict(good, **changes)
                with mock.patch.dict(wiring.RUNNERS, {"fake": lambda *a: result}), \
                        mock.patch.object(wiring, "forget"):
                    self.assertEqual(wiring.run("fake", "W4")[2], passes)

    def test_behavior_failure_artifact_keeps_exit_status_and_diagnostics(self):
        failed = {"transcript": "", "text": "", "code": 124, "session": None, "events": [],
                  "stderr": "Harness timed out", "stdout": "partial provider output"}
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(behavior, "ROOT", folder), \
                mock.patch.dict(behavior.RUNNERS, {"fake": lambda *a, **k: failed}), \
                mock.patch.object(behavior, "evaluate", return_value=["agent run failed"]):
            result = behavior.run_one("fake", "N1", 0, None)
            self.assertTrue(result[3])
            with open(os.path.join(folder, "tests", "out", "behavior-fake-N1-0.json")) as handle:
                artifact = json.load(handle)
            self.assertEqual(artifact["code"], 124)
            self.assertEqual(artifact["stderr"], "Harness timed out")
            self.assertEqual(artifact["stdout"], "partial provider output")

    def test_live_entrypoints_reject_ci_before_launching_a_harness(self):
        for entrypoint in ("wiring.py", "behavior.py"):
            for marker in ("CI", "GITHUB_ACTIONS"):
                with self.subTest(entrypoint=entrypoint, marker=marker):
                    env = dict(os.environ)
                    env.pop("CI", None)
                    env.pop("GITHUB_ACTIONS", None)
                    env[marker] = "false"  # Presence is enough, matching the production guard.
                    result = subprocess.run([sys.executable, os.path.join(ROOT, "tests", entrypoint)],
                                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("local-only", result.stderr)

    def test_all_runners_preserve_partial_timeout_results_before_context_exists(self):
        records = {
            "claude": {"type": "result", "result": "partial response"},
            "codex": {"type": "item.completed", "item": {"type": "agent_message", "text": "partial response"}},
            "opencode": {"type": "text", "sessionID": "synthetic", "part": {"text": "partial response"}},
        }
        for name, event in records.items():
            with self.subTest(harness=name), tempfile.TemporaryDirectory() as folder:
                output = json.dumps(event) + '\n{"incomplete":'
                timed_out = subprocess.CompletedProcess([], 124, output, "Harness timed out")
                with mock.patch.object(harness, "run_process", return_value=timed_out), \
                        mock.patch.object(harness, "codex_home", return_value=os.path.join(folder, "codex-home")):
                    result = harness.RUNNERS[name](folder, folder, "test prompt", plugin=False)
                self.assertEqual(result["code"], 124)
                self.assertEqual(result["text"], "partial response")
                self.assertEqual(result["stdout"], output)
                self.assertIn("Harness timed out", result["stderr"])
                self.assertEqual(result["transcript"], "")


@unittest.skipUnless(os.name == "posix", "local harness runners require POSIX")
class ProcessLifecycleTest(unittest.TestCase):
    def assert_stopped(self, pid):
        # A killed child can briefly remain as a zombie until the host reaps it.
        for _ in range(100):
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
            if not status or status.startswith("Z"):
                return
            time.sleep(0.01)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.fail("harness child survived process-group cleanup")

    def program(self, wait):
        return ("import json,subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],"
                "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                "print(json.dumps({'type':'ready','pid':child.pid}),flush=True); "
                "print('provider diagnostic',file=sys.stderr,flush=True); "
                + ("print('{\"partial\":',flush=True); time.sleep(30)" if wait else ""))

    def test_timeout_kills_children_and_preserves_partial_output(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_process([sys.executable, "-c", self.program(True)], folder, dict(os.environ), 2)
        self.assertEqual(result.returncode, 124)
        self.assertIn("provider diagnostic", result.stderr)
        self.assertIn("Harness timed out", result.stderr)
        self.assertIn('{"partial":', result.stdout)
        events = json_events(result)
        self.assertEqual(len(events), 1)
        self.assert_stopped(events[0]["pid"])

    def test_normal_exit_also_cleans_up_background_children(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_process([sys.executable, "-c", self.program(False)], folder, dict(os.environ), 10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_stopped(json_events(result)[0]["pid"])

    def test_missing_executable_is_an_explicit_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_process([os.path.join(folder, "missing-harness")], folder, dict(os.environ), 1)
        self.assertEqual(result.returncode, 127)
        self.assertTrue(result.stderr)

    def test_successful_process_cannot_hide_malformed_json(self):
        result = subprocess.CompletedProcess([], 0, '{"partial":', "")
        with self.assertRaises(ValueError):
            json_events(result)


if __name__ == "__main__":
    unittest.main()

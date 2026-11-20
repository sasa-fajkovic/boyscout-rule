import copy
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "hooks", "boyscout.py")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
PYTHON = os.environ.get("BOYSCOUT_TEST_PYTHON", sys.executable)
WRONG = "Exit code 1\nerror: unknown command"
MISSING_SCRIPT = 'Exit code 1\nnpm error Missing script: "tests"'


def fixture(harness, name):
    with open(os.path.join(FIXTURES, harness, name + ".json")) as handle:
        return json.load(handle)


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(self.home, ".claude"))
        os.makedirs(os.path.join(self.home, ".codex"))
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.home,
            "CLAUDE_PLUGIN_DATA": self.data,
        }
        self.session = "test-session-" + os.path.basename(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_hook(self, harness, event, payload, env=None, raw=None, budget=5.0):
        """budget only catches a stuck hook on a slow runner; assertFast checks the 100 ms latency itself."""
        merged = dict(self.env)
        merged.update(env or {})
        data = raw if raw is not None else json.dumps(payload)
        started = time.time()
        runner = [PYTHON, "-I"]
        if os.environ.get("BOYSCOUT_COVERAGE") == "1":
            runner += ["-m", "coverage", "run", "--parallel-mode"]
        result = subprocess.run(runner + [SCRIPT, harness, event], input=data, env=merged,
                                capture_output=True, text=True, timeout=10)
        elapsed = time.time() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertLess(elapsed, budget)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def payload(self, harness, name, **changes):
        value = copy.deepcopy(fixture(harness, name))
        value["session_id"] = self.session
        value.update(changes)
        return value

    def shell(self, command, ok=True, harness="claude", budget=5.0, error=WRONG):
        if harness == "claude":
            name = "PostToolUse" if ok else "PostToolUseFailure"
            value = self.payload("claude", name)
            value["tool_input"] = {"command": command, "description": ""}
            if not ok:
                value["error"] = error
            return self.run_hook("claude", name, value, budget=budget)
        raise AssertionError("use codex_shell")

    def prompt(self, text, harness="claude"):
        value = self.payload(harness, "UserPromptSubmit", prompt=text)
        return self.run_hook(harness, "UserPromptSubmit", value)

    def startup(self):
        """Time of a trivial hook call, so latency checks measure the hook's own work on slow runners."""
        started = time.time()
        self.run_hook("claude", "UserPromptSubmit", self.payload("claude", "UserPromptSubmit", session_id="startup", prompt="ok"))
        return time.time() - started

    def assertFast(self, call, limit=0.1, tries=3):
        """call() makes one hook call; its time beyond a trivial call's must stay under limit in one of a few tries,
        so a busy CI runner does not fail it."""
        times = []
        for _ in range(tries):
            startup = self.startup()
            started = time.time()
            call()
            times.append(time.time() - started - startup)
            if times[-1] < limit:
                return
        self.fail("hook took %s beyond startup" % ["%.3f" % t for t in times])

    def context(self, output):
        self.assertIsNotNone(output)
        return output["hookSpecificOutput"]["additionalContext"]

    def state_files(self):
        folder = os.path.join(self.data, "sessions")
        return [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".json")] if os.path.isdir(folder) else []


class SessionStartTest(HookTest):
    def test_u1_output_shape_both_harnesses(self):
        for harness in ("claude", "codex"):
            out = self.run_hook(harness, "SessionStart", self.payload(harness, "SessionStart"),
                                env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
            self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
            text = self.context(out)
            self.assertIn("boyscout-rule skill", text)
            self.assertRegex(text, r"Session id for branch names: [0-9a-f]{10}\.")
            self.assertLess(len(json.dumps(out)), 10000)

    def test_u2_claude_memory_warning(self):
        start = self.payload("claude", "SessionStart", cwd=self.tmp)
        self.assertIn("systemMessage", self.run_hook("claude", "SessionStart", start))
        self.assertNotIn("systemMessage", self.run_hook("claude", "SessionStart", start, env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}))
        user = os.path.join(self.home, ".claude", "settings.json")
        with open(user, "w") as handle:
            json.dump({"autoMemoryEnabled": False}, handle)
        self.assertNotIn("systemMessage", self.run_hook("claude", "SessionStart", start))
        os.makedirs(os.path.join(self.tmp, ".claude"))
        with open(os.path.join(self.tmp, ".claude", "settings.json"), "w") as handle:
            json.dump({"autoMemoryEnabled": True}, handle)
        self.assertIn("systemMessage", self.run_hook("claude", "SessionStart", start))
        with open(os.path.join(self.tmp, ".claude", "settings.local.json"), "w") as handle:
            json.dump({"env": {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}}, handle)
        self.assertNotIn("systemMessage", self.run_hook("claude", "SessionStart", start))

    def test_u2_codex_memory_warning(self):
        start = self.payload("codex", "SessionStart")
        config = os.path.join(self.home, ".codex", "config.toml")
        self.assertNotIn("systemMessage", self.run_hook("codex", "SessionStart", start))
        with open(config, "w") as handle:
            handle.write('model = "x"\n[features]\nmemories = true\n')
        self.assertIn("systemMessage", self.run_hook("codex", "SessionStart", start))
        with open(config, "w") as handle:
            handle.write('[features]\nmemories = false # off\n')
        self.assertNotIn("systemMessage", self.run_hook("codex", "SessionStart", start))
        codex_home = os.path.join(self.tmp, "codex-home")
        os.makedirs(codex_home)
        with open(os.path.join(codex_home, "config.toml"), "w") as handle:
            handle.write('features.memories = true\n')
        self.assertIn("systemMessage", self.run_hook("codex", "SessionStart", start, env={"CODEX_HOME": codex_home}))

    def test_u3_context_on_every_source_warning_only_at_startup(self):
        for source in ("resume", "clear", "compact"):
            out = self.run_hook("claude", "SessionStart", self.payload("claude", "SessionStart", source=source, cwd=self.tmp))
            self.assertIn("boyscout-rule skill", self.context(out))
            self.assertNotIn("systemMessage", out)


class FailureTest(HookTest):
    def test_u4_fail_then_different_fix(self):
        self.assertIsNone(self.shell("npm run tests", ok=False))
        self.assertIn("npm failed", self.context(self.shell("npm test")))

    def test_u4_fix_within_window_counts_other_calls(self):
        self.shell("make buidl", ok=False)
        for _ in range(3):
            self.shell("node scripts/check.js")
        self.assertIsNotNone(self.shell("make build"))

    def test_u4_fix_outside_window_is_ignored(self):
        self.shell("make buidl", ok=False)
        for _ in range(5):
            self.shell("node scripts/check.js")
        self.assertIsNone(self.shell("make build"))

    def test_read_only_commands_do_not_use_up_the_window(self):
        self.shell("make buidl", ok=False)
        for _ in range(6):
            self.shell("ls src")
        self.assertIsNotNone(self.shell("make build"))

    def test_u5_no_nudge_cases(self):
        self.shell("npm test", ok=False)
        self.assertIsNone(self.shell("npm test"))
        self.shell("pytest -x", ok=False)
        for _ in range(6):
            self.shell("node scripts/check.js")
        self.assertIsNone(self.shell("pytest"))
        self.shell("cargo build", ok=False)
        self.assertIsNone(self.shell("make build"))
        interrupted = self.payload("claude", "PostToolUseFailure", is_interrupt=True)
        interrupted["tool_input"] = {"command": "go test ./..."}
        self.assertIsNone(self.run_hook("claude", "PostToolUseFailure", interrupted))
        self.assertIsNone(self.shell("go test ./pkg"))
        self.shell("grep foo x", ok=False)
        self.assertIsNone(self.shell("grep -r foo ."))
        self.shell("pnpm test 2>&1 | tail -20", ok=False)
        self.assertIsNone(self.shell("pnpm test"))

    def test_piped_failure_is_detected_from_output(self):
        value = self.payload("claude", "PostToolUse")
        value["tool_input"] = {"command": "cat package.json && npm run tests 2>&1 | tail -30"}
        value["tool_response"] = {"stdout": 'npm error Missing script: "tests"\nnpm error To see a list of scripts', "stderr": ""}
        self.assertIsNone(self.run_hook("claude", "PostToolUse", value))
        self.assertIn("npm failed", self.context(self.shell("npm test")))

    def test_exit_status_echo_is_detected(self):
        value = self.payload("claude", "PostToolUse")
        value["tool_input"] = {"command": 'make buidl; echo "exit=$?"'}
        value["tool_response"] = {"stdout": "make: *** No rule to make target `buidl'.  Stop.\nexit=2", "stderr": ""}
        self.assertIsNone(self.run_hook("claude", "PostToolUse", value))
        self.assertIn("make failed", self.context(self.shell("make build; echo \"exit=$?\"")))

    def test_trailing_command_failure_does_not_hide_a_fix(self):
        self.shell("npm run tests", ok=False)
        value = self.payload("claude", "PostToolUseFailure")
        value["tool_input"] = {"command": "npm test; git status --short; rg --files | rg missing"}
        value["error"] = "Exit code 1\n> demo test\nall tests pass"
        self.assertIn("npm failed", self.context(self.run_hook("claude", "PostToolUseFailure", value)))

    def test_unpiped_error_text_is_not_a_failure(self):
        value = self.payload("claude", "PostToolUse")
        value["tool_input"] = {"command": "npm run lint"}
        value["tool_response"] = {"stdout": "error: 3 problems found", "stderr": ""}
        self.run_hook("claude", "PostToolUse", value)
        self.assertIsNone(self.shell("npm run lint -- --fix"))

    def test_u5_subagent_ignored(self):
        failed = self.payload("claude", "PostToolUseFailure", agent_id="abc")
        failed["tool_input"] = {"command": "npm run tests"}
        self.assertIsNone(self.run_hook("claude", "PostToolUseFailure", failed))
        self.assertIsNone(self.shell("npm test"))

    def test_u4_doc_command_then_real_failure_then_fix(self):
        self.shell("npm run tests", ok=False)
        self.assertIsNone(self.shell("npm test", ok=False))
        self.assertIsNotNone(self.shell("npm test"))

    def test_u6_once_per_program(self):
        self.shell("npm run tests", ok=False)
        self.assertIsNotNone(self.shell("npm test"))
        self.shell("npm run lnt", ok=False)
        self.assertIsNone(self.shell("npm run lint"))

    def test_program_detection_skips_setup_and_env(self):
        self.shell("cd app && FOO=1 npm run tests", ok=False)
        self.assertIsNotNone(self.shell("cd app && npm test"))

    def test_codex_exit_code_from_transcript(self):
        transcript = os.path.join(FIXTURES, "codex", "transcript.jsonl")
        with open(transcript) as handle:
            items = [json.loads(line)["payload"]["item"] for line in handle]
        failed, passed = items[0], items[1]
        def call(item, command, output):
            value = self.payload("codex", "PostToolUse", transcript_path=transcript, tool_use_id=item["id"], tool_response=output)
            value["tool_input"] = {"command": command}
            return self.run_hook("codex", "PostToolUse", value)
        self.assertIsNone(call(failed, "make buidl", "make: *** No rule to make target 'buidl'.  Stop.\n"))
        self.assertIn("make failed", self.context(call(passed, "make build", "built\n")))

    def test_codex_without_exit_code_judges_output(self):
        for path in (os.path.join(self.tmp, "missing.jsonl"), None):
            self.session = "s-%s" % path
            value = self.payload("codex", "PostToolUse", transcript_path=path,
                                 tool_response="make: *** No rule to make target 'buidl'.  Stop.\n")
            value["tool_input"] = {"command": "make buidl"}
            self.assertIsNone(self.run_hook("codex", "PostToolUse", value))
            value["tool_input"] = {"command": "make build"}
            self.assertIsNone(self.run_hook("codex", "PostToolUse", value))
            value["tool_response"] = "built\n"
            self.assertIn("make failed", self.context(self.run_hook("codex", "PostToolUse", value)))


class CorrectionTest(HookTest):
    def test_u7_triggers(self):
        for text in ("I told you to use the Makefile", "again?", "that's wrong, the path is src/app",
                     "`npm test` is wrong; use `pnpm test`.", "<file>README</file>\nI told you to use pnpm.",
                     "why did you run npm"):
            self.session = "s-" + str(abs(hash(text)))
            self.assertIsNotNone(self.prompt(text), text)

    def test_u7_non_triggers(self):
        for text in ("no worries, continue", "do it again, thanks", "No problem", "Please add a test", "Could you try again?",
                     "```\nno such file\n```\nwhat does this mean", "> no, wrong\nquoted text above",
                     "<agent-message from=\"x\">no, wrong</agent-message>", "/review no", "nothing to add",
                     "no, use pnpm here", "Nope, the other one", "No. Use pnpm."):
            self.session = "s-" + str(abs(hash(text)))
            self.assertIsNone(self.prompt(text), text)

    def test_u7_handback_fixture_ignored(self):
        value = self.payload("claude", "UserPromptSubmit-handback")
        self.assertIsNone(self.run_hook("claude", "UserPromptSubmit", value))

    def test_u7_rate_limit(self):
        self.assertIsNotNone(self.prompt("I told you to use pnpm"))
        for text in ("ok", "that's wrong", "fine", "go on"):
            self.assertIsNone(self.prompt(text))
        self.assertIsNotNone(self.prompt("I told you already"))

    def test_u7_codex(self):
        self.assertIsNotNone(self.prompt("I told you to use pnpm", harness="codex"))


class RefusalTest(HookTest):
    def refused(self, command, error):
        value = self.payload("claude", "PostToolUseFailure", error=error)
        value["tool_input"] = {"command": command}
        return self.run_hook("claude", "PostToolUseFailure", value)

    def test_refused_retry_after_wrong_form_does_not_nudge(self):
        self.shell("npm run tests", ok=False, error=MISSING_SCRIPT)
        self.assertIsNone(self.refused("npm test", "Permission to use Bash with command npm test has been denied."))
        self.assertIn("npm failed", self.context(self.shell("npm test")))

    def test_refusal_never_starts_a_window(self):
        self.assertIsNone(self.refused("npm run tests", 'Blocked by hook: npm error Missing script: "tests"'))
        self.assertIsNone(self.shell("npm test"))

    def test_refusals_do_not_use_up_the_window(self):
        self.shell("npm run tests", ok=False, error=MISSING_SCRIPT)
        for _ in range(6):
            self.assertIsNone(self.refused("node build.js", "The user doesn't want to proceed with this tool use."))
        self.assertIn("npm failed", self.context(self.shell("npm test")))

    def test_exit_header_must_be_a_line_of_its_own(self):
        self.shell("npm run tests", ok=False, error=MISSING_SCRIPT)
        self.assertIsNone(self.refused("npm test", "Exit code 1 is not allowed by the sandbox policy"))
        self.assertIn("npm failed", self.context(self.refused("npm test", "Exit code 1\r\n1 failing")))

    def test_masked_shell_not_found_is_a_wrong_form(self):
        value = self.payload("claude", "PostToolUse")
        value["tool_input"] = {"command": "bin/check 2>&1 | tail -1"}
        value["tool_response"] = {"stdout": "sh: 1: bin/check: not found", "stderr": ""}
        self.assertIsNone(self.run_hook("claude", "PostToolUse", value))
        self.assertIn("check failed", self.context(self.shell("bin/check --all ./src")))

    def test_failures_recorded_by_the_previous_version(self):
        self.shell("node scripts/check.js")
        (path,) = self.state_files()
        key = hashlib.sha256(b"npm").hexdigest()[:16]
        for wrong, expected in ((False, False), (True, True)):
            with open(path) as handle:
                state = json.load(handle)
            state["failures"] = {key: [{"hash": "0" * 16, "at": state["calls"], "wrong": wrong}]}
            with open(path, "w") as handle:
                json.dump(state, handle)
            self.assertEqual(self.shell("npm test") is not None, expected, wrong)

    def test_ordinary_failure_never_starts_a_window(self):
        self.shell("npm test", ok=False, error="Exit code 1\n1 failing")
        self.assertIsNone(self.shell("npm test test/math.js"))
        self.shell("node -e 'process.exit(3)'", ok=False, error="Exit code 3")
        self.assertIsNone(self.shell("node build.js"))

    def test_missing_tool_is_not_a_wrong_form(self):
        self.shell("pnpm test", ok=False, error="Exit code 127\nbash: pnpm: command not found")
        self.assertIsNone(self.shell("pnpm run test"))
        self.session += "-b"
        self.shell("bin/check", ok=False, error="Exit code 127\nsh: 1: bin/check: not found")
        self.assertIn("check failed", self.context(self.shell("bin/check --all ./src")))

    def test_new_wrong_forms(self):
        for command, error in (("python3 tools/gen.py", "python3: can't open file '/repo/tools/gen.py': [Errno 2] No such file"),
                               ("python3 gen.py --fast", "gen.py: error: unrecognized arguments: --fast"),
                               ("python3 gen.py bulid", "gen.py: error: argument cmd: invalid choice: 'bulid'"),
                               ("python3 gen.py lint", "error: unknown subcommand 'lint'")):
            self.session = "s-" + str(abs(hash(error)))
            self.shell(command, ok=False, error="Exit code 2\n" + error)
            self.assertIn("python3 failed", self.context(self.shell("python3 scripts/gen.py build")), error)


class LexerTest(HookTest):
    def test_backslash_continuation_is_one_command(self):
        self.shell("make \\\n  buidl", ok=False, error="Exit code 2\nmake: *** No rule to make target 'buidl'.  Stop.")
        self.assertIn("make failed", self.context(self.shell("make build")))

    def test_continuation_flag_line_is_not_a_program(self):
        self.shell("npm run tests\n  --verbose", ok=False, error=MISSING_SCRIPT)
        self.assertIn("npm failed", self.context(self.shell("npm test")))

    def test_shell_loops_are_skipped(self):
        self.shell("npm run tests", ok=False, error=MISSING_SCRIPT)
        for loop in ("for f in a b; do node $f; done", "while read l; do make $l; done < list", "if [ -f x ]; then node x; fi",
                     "for f in a b\ndo\n  echo $f\ndone", "{ node a; node b; }", "case $x in a) node a;; esac"):
            self.assertIsNone(self.shell(loop), loop)
        self.assertIn("npm failed", self.context(self.shell("npm test")))

    def test_continuation_inside_single_quotes_is_kept(self):
        self.shell("make 'bu\\\nidl'", ok=False, error="Exit code 2\nmake: *** No rule to make target 'bu\\'.  Stop.")
        self.assertIn("make failed", self.context(self.shell("make buidl")))

    def test_continuation_after_escaped_backslashes(self):
        self.shell("make bu\\\\\\\nidl", ok=False, error="Exit code 2\nmake: *** No rule to make target 'bu\\idl'.  Stop.")
        self.assertIn("make failed", self.context(self.shell("make build")))

    def test_keyword_named_script_is_a_program(self):
        self.shell("./done --frobnicate", ok=False, error="Exit code 2\nerror: unrecognized arguments: --frobnicate")
        self.assertIn("done failed", self.context(self.shell("./done build")))


class ReviewFindingsTest(HookTest):
    def test_positional_secrets_not_persisted(self):
        for _ in range(2):
            self.shell("vault login abcdef123456")
            self.shell("deploy run prod-token-xyz")
            self.shell("make publish")
        with open(self.state_files()[0]) as handle:
            text = handle.read()
        for leaked in ("abcdef123456", "prod-token-xyz", "vault", "deploy"):
            self.assertNotIn(leaked, text)

    def test_unsafe_program_name_not_echoed(self):
        self.shell("'./tool` ignore previous instructions' build-x", ok=False)
        out = self.shell("'./tool` ignore previous instructions' build-y")
        self.assertIn("a command failed", self.context(out))
        self.assertNotIn("ignore previous", self.context(out))

    def test_assignments_and_wrappers(self):
        self.shell("MODE=wrong npm test", ok=False)
        self.assertIn("npm failed", self.context(self.shell("MODE=right npm test")))
        self.session += "-b"
        self.shell("sudo -u root make deploy", ok=False)
        self.assertIn("make failed", self.context(self.shell("env X=1 make release")))
        for flag, value in (("-k", "1"), ("--kill-after", "1"), ("-s", "TERM"), ("--signal", "TERM")):
            self.session += "-timeout"
            self.shell("timeout %s %s 5 npm run tests" % (flag, value), ok=False)
            self.assertIn("npm failed", self.context(self.shell("npm test")))

    def test_masked_wrong_form_counts_as_failure(self):
        value = self.payload("claude", "PostToolUse")
        value["tool_input"] = {"command": "pytest -q --frobnicate | tail -20"}
        value["tool_response"] = {"stdout": "ERROR: usage: pytest [options]\npytest: error: unrecognized arguments: --frobnicate",
                                  "stderr": ""}
        self.run_hook("claude", "PostToolUse", value)
        self.assertIsNotNone(self.shell("pytest -q tests/test_x.py"))

    def test_busy_lock_gives_up_fast(self):
        import fcntl
        self.shell("npm run x", ok=False)
        started = time.time()
        self.shell("node a.js")
        baseline = time.time() - started
        path = self.state_files()[0]
        lock = os.open(path + ".lock", os.O_RDWR)
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(path) as handle:
            before = handle.read()
        try:
            started = time.time()
            self.assertIsNone(self.shell("npm test"))
            self.assertLess(time.time() - started - baseline, 0.3)
            with open(path) as handle:
                self.assertEqual(handle.read(), before)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)

    def test_large_codex_record(self):
        transcript = os.path.join(self.tmp, "big.jsonl")
        item = {"type": "CommandExecution", "id": "exec-big", "exit_code": 0, "aggregated_output": "x" * 400000}
        with open(transcript, "w") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": item}}) + "\n")
            ok = dict(item, id="exec-ok", exit_code=0, aggregated_output="ok")
            handle.write(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": ok}}) + "\n")
        def call(call_id, command, output):
            value = self.payload("codex", "PostToolUse", transcript_path=transcript, tool_use_id=call_id, tool_response=output)
            value["tool_input"] = {"command": command}
            return self.run_hook("codex", "PostToolUse", value)
        self.assertFast(lambda: self.assertIsNone(call("exec-big", "make buidl", "make: *** No rule to make target 'buidl'.  Stop.\n")))
        self.assertIsNone(call("exec-ok", "make build", "built\n"))

    def test_line_start_patterns_stay_fast_on_blank_output(self):
        def call(output):
            value = self.payload("codex", "PostToolUse", transcript_path=None, tool_response=output)
            value["tool_input"] = {"command": "npm test"}
            self.run_hook("codex", "PostToolUse", value)
        self.assertFast(lambda: call("\n" * 4000 + " \n" * 2000))
        self.assertFast(lambda: call("cd " * 2600))

    def test_short_session_ids_do_not_collide(self):
        a = self.run_hook("claude", "SessionStart", self.payload("claude", "SessionStart", session_id="01a18863-aaaa"))
        b = self.run_hook("claude", "SessionStart", self.payload("claude", "SessionStart", session_id="01a18863-bbbb"))
        self.assertNotEqual(self.context(a), self.context(b))


class RoundTwoTest(HookTest):
    def test_heredoc_body_is_not_commands(self):
        self.shell("node - <<'JS'\nrequire('./missing')\nmake('x')\nJS", ok=False,
                   error="Exit code 1\nError: Cannot find module './missing'")
        self.assertIn("node failed", self.context(self.shell("node build.js")))

    def test_or_branches_not_counted(self):
        self.shell("make buidl || node build.js", ok=False, error="Exit code 2\nmake: *** No rule to make target 'buidl'.  Stop.")
        self.assertIn("make failed", self.context(self.shell("make build")))

    def test_zero_failed_is_success(self):
        value = self.payload("claude", "PostToolUse")
        value["tool_input"] = {"command": "pytest -q | tail -20"}
        value["tool_response"] = {"stdout": "0 failed, 3 passed in 0.1s", "stderr": ""}
        self.run_hook("claude", "PostToolUse", value)
        self.assertIsNone(self.shell("pytest -q tests/test_x.py"))

    def test_more_wrappers_and_modules(self):
        self.shell("sudo --user root make deploy", ok=False)
        self.assertIn("make failed", self.context(self.shell("make release")))

    def test_huge_command_is_skipped_fast(self):
        started = time.time()
        self.assertIsNone(self.shell("echo " + "x && y " * 40000))
        self.assertLess(time.time() - started, 1.0)

    def test_h3_more_cases(self):
        self.assertIsNone(self.prompt("No, thanks."))
        self.session += "-b"
        self.assertIsNotNone(self.prompt("<system-reminder>context</system-reminder>\nI told you to use pnpm."))



class RoundThreeTest(HookTest):
    def test_failed_setup_is_not_blamed_on_the_program(self):
        value = self.payload("claude", "PostToolUseFailure-cd")
        self.assertIsNone(self.run_hook("claude", "PostToolUseFailure", value))
        self.assertIsNone(self.shell("npm test"))
        self.shell("cd app && npm run tests", ok=False)
        self.assertIn("npm failed", self.context(self.shell("cd app && npm test")))

    def test_codex_shell_list_command(self):
        def call(command, output):
            value = self.payload("codex", "PostToolUse", transcript_path=None, tool_response=output)
            value["tool_input"] = {"command": command}
            return self.run_hook("codex", "PostToolUse", value)
        self.assertIsNone(call(["/bin/zsh", "-lc", "npm run tests"], 'npm error Missing script: "tests"\n'))
        self.assertIn("npm failed", self.context(call(["/bin/zsh", "-lc", "npm test"], "all tests pass\n")))

    def test_unclosed_tags_are_fast(self):
        tries = iter(range(10))

        def call():
            self.session = "unclosed-%d" % next(tries)
            self.assertIsNotNone(self.prompt("I told you to use pnpm\n" + "<div class=x>\n" * 30000))
        self.assertFast(call)
        self.session = "s-closed"
        self.assertIsNone(self.prompt("<a>x</a>" * 5000 + "<b>no, wrong</b>"))

    def test_sentence_start_said(self):
        self.assertIsNotNone(self.prompt("I said use pnpm."))
        self.session = "s-ticket"
        self.assertIsNone(self.prompt("As I said in the ticket, please add tests"))

    def test_context_grants_scoped_permission_and_respects_prohibitions(self):
        out = self.run_hook("claude", "SessionStart", self.payload("claude", "SessionStart"), env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
        text = self.context(out)
        for phrase in ("invoke the boyscout-rule skill before deciding how to handle it",
                       "including during check or explain tasks", "never apply that fix in the user's checkout",
                       "standing request to make qualifying fixes in a separate worktree",
                       "push `boyscout/*` branches to this repo's origin and open draft PRs",
                       'rules such as "push only when asked" or "only make changes I ask for" wait for',
                       "no reason to skip", "Skip only for an explicit prohibition or approval requirement",
                       '"ask before every push"', "whether or not it names boyscout",
                       "report the finding and leave its files untouched"):
            self.assertIn(phrase, text)

    def test_session_end_removes_lock(self):
        self.shell("npm run tests", ok=False)
        self.run_hook("claude", "SessionEnd", self.payload("claude", "SessionEnd"))
        self.assertEqual(os.listdir(os.path.join(self.data, "sessions")), [])



class RoundFourTest(HookTest):
    def codex(self, command, output):
        value = self.payload("codex", "PostToolUse", transcript_path=None, tool_response=output)
        value["tool_input"] = {"command": command}
        return self.run_hook("codex", "PostToolUse", value)

    def test_codex_fallback_ignores_failed_setup(self):
        self.assertIsNone(self.codex("cd /missing && npm run tests", "(eval):cd:1: no such file or directory: /missing\n"))
        self.assertIsNone(self.codex("cd app && npm test", "all tests pass\n"))

    def test_codex_fallback_ignores_error_text_from_a_working_command(self):
        self.assertIsNone(self.codex("npm run lint", "error: 3 problems\n"))
        self.assertIsNone(self.codex("npm test", "all tests pass\n"))

    def test_missing_sourced_file_is_not_blamed_on_the_program(self):
        value = self.payload("claude", "PostToolUseFailure", error="Exit code 1\nbash: env.sh: No such file or directory")
        value["tool_input"] = {"command": "source env.sh && npm run tests"}
        self.assertIsNone(self.run_hook("claude", "PostToolUseFailure", value))
        self.assertIsNone(self.shell("npm test"))

    def test_failure_of_a_later_program_is_not_blamed_on_the_first(self):
        value = self.payload("claude", "PostToolUseFailure", error="Exit code 1\nbuilt\n1 failed, 2 passed in 0.1s")
        value["tool_input"] = {"command": "npm run build && pytest -q"}
        self.assertIsNone(self.run_hook("claude", "PostToolUseFailure", value))
        self.assertIsNone(self.shell("npm test"))
        self.assertIsNone(self.shell("pytest"))

    def test_malformed_tags_are_fast(self):
        tries = iter(range(30))
        for text in ("<a" * 49999, "<a " * 33000, "<div class=x\n" * 20000):
            def call():
                self.session = "malformed-%d" % next(tries)
                self.assertIsNotNone(self.prompt("I told you, use pnpm " + text))
            self.assertFast(call)


class RoundFiveTest(HookTest):
    def test_directions_count_only_after_the_first_prompt(self):
        for text in ("We don't use npm in this repo.", "We don\u2019t use npm here", "We no longer support Node 16.",
                     "I never use mocks for the db layer", "The setup section is outdated"):
            self.session = "s-" + str(abs(hash(text)))
            self.assertIsNone(self.prompt(text), text)
            self.assertIsNotNone(self.prompt(text), text)

    def test_plain_instructions_are_not_directions(self):
        for text in ("We use pnpm in this repo.", "Don't run the full test suite, it is slow", "Don't use mocks for the db layer"):
            self.session = "s-" + str(abs(hash(text)))
            self.prompt("Please add a test.")
            self.assertIsNone(self.prompt(text), text)

    def test_bare_no_is_not_a_correction(self):
        for text in ("No tests exist for foo yet, please add some.", "Please add tests.\nNo tests exist yet.", "No, thanks.",
                     "no", "Nope, wrong one", "No. Use pnpm."):
            self.session = "s-" + str(abs(hash(text)))
            self.assertIsNone(self.prompt(text), text)

    def test_interrupted_call_is_not_a_success(self):
        self.shell("npm run tests", ok=False)
        value = self.payload("claude", "PostToolUse")
        value["tool_input"] = {"command": "npm run dev"}
        value["tool_response"] = dict(value["tool_response"], interrupted=True)
        self.assertIsNone(self.run_hook("claude", "PostToolUse", value))

    def test_codex_fallback_unknown_on_test_failures(self):
        def call(command, output):
            value = self.payload("codex", "PostToolUse", transcript_path=None, tool_response=output)
            value["tool_input"] = {"command": command}
            return self.run_hook("codex", "PostToolUse", value)
        self.assertIsNone(call("npm run tests", 'npm error Missing script: "tests"\n'))
        self.assertIsNone(call("npm test", "1 failed, 2 passed\n"))
        self.assertIsNone(call("mytool --help", "usage: mytool [build]\n"))
        self.assertIsNone(call("mytool build", "built\n"))

    def test_same_program_compound_failure_is_unknown(self):
        value = self.payload("claude", "PostToolUseFailure", error="Exit code 1\n1 failed")
        value["tool_input"] = {"command": "npm run build && npm test"}
        self.assertIsNone(self.run_hook("claude", "PostToolUseFailure", value))
        self.assertIsNone(self.shell("npm test"))

    def test_without_fcntl_only_session_start_runs(self):
        runner = ("import sys; sys.modules['fcntl'] = None; sys.argv = ['boyscout.py'] + sys.argv[1:]; "
                  "exec(compile(open(%r).read(), 'boyscout.py', 'exec'), {'__name__': '__main__'})" % SCRIPT)
        def run(event, payload):
            result = subprocess.run([PYTHON, "-I", "-c", runner, "claude", event], input=json.dumps(payload),
                                    env=dict(self.env, CLAUDE_CODE_DISABLE_AUTO_MEMORY="1"), capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            return result.stdout
        self.assertIn("boyscout-rule skill", run("SessionStart", self.payload("claude", "SessionStart")))
        failure = self.payload("claude", "PostToolUseFailure")
        failure["tool_input"] = {"command": "npm run tests"}
        self.assertEqual(run("PostToolUseFailure", failure), "")
        self.assertEqual(run("UserPromptSubmit", self.payload("claude", "UserPromptSubmit", prompt="I told you to use pnpm")), "")
        self.assertEqual(run("SessionEnd", self.payload("claude", "SessionEnd")), "")
        self.assertEqual(self.state_files(), [])


class RoundSixTest(HookTest):
    def test_background_launch_is_unknown(self):
        self.shell("npm run dev", ok=False)
        value = self.payload("claude", "PostToolUse-background")
        value["tool_input"] = dict(value["tool_input"], command="npm run start")
        self.assertIsNone(self.run_hook("claude", "PostToolUse", value))

    def test_failed_guard_is_not_blamed_on_the_program(self):
        value = self.payload("claude", "PostToolUseFailure", error="Exit code 1")
        value["tool_input"] = {"command": "test -f /missing-config && npm test"}
        self.assertIsNone(self.run_hook("claude", "PostToolUseFailure", value))
        self.assertIsNone(self.shell("npm run lint"))
        value = self.payload("claude", "PostToolUseFailure", error='Exit code 1\nnpm error Missing script: "tests"')
        value["tool_input"] = {"command": "test -f package.json && npm run tests"}
        self.run_hook("claude", "PostToolUseFailure", value)
        self.assertIn("npm failed", self.context(self.shell("npm test")))

    def test_cleanup_before_program_is_not_a_guard(self):
        value = self.payload("claude", "PostToolUseFailure", error="Exit code 4\nERROR: usage: pytest [options]\n"
                             "pytest: error: unrecognized arguments: --frobnicate")
        value["tool_input"] = {"command": "rm -rf .pytest_cache && pytest --frobnicate"}
        self.assertIsNone(self.run_hook("claude", "PostToolUseFailure", value))
        self.assertIn("pytest failed", self.context(self.shell("pytest tests/")))

    def test_wrong_form_then_running_form_nudges_even_if_it_fails(self):
        value = self.payload("claude", "PostToolUseFailure", error='Exit code 1\nnpm error Missing script: "tests"')
        value["tool_input"] = {"command": "npm run tests"}
        self.assertIsNone(self.run_hook("claude", "PostToolUseFailure", value))
        value = self.payload("claude", "PostToolUseFailure", error="Exit code 1\nAssertionError: add\n1 failing")
        value["tool_input"] = {"command": "npm test"}
        self.assertIn("npm failed", self.context(self.run_hook("claude", "PostToolUseFailure", value)))

    def test_two_ordinary_failures_do_not_nudge(self):
        value = self.payload("claude", "PostToolUseFailure", error="Exit code 1\n1 failing")
        value["tool_input"] = {"command": "npm test"}
        self.run_hook("claude", "PostToolUseFailure", value)
        value["tool_input"] = {"command": "npm test test/math.js"}
        self.assertIsNone(self.run_hook("claude", "PostToolUseFailure", value))

    def test_codex_wrong_form_then_test_failure_nudges(self):
        def call(command, output):
            value = self.payload("codex", "PostToolUse", transcript_path=None, tool_response=output)
            value["tool_input"] = {"command": command}
            return self.run_hook("codex", "PostToolUse", value)
        self.assertIsNone(call("npm run tests", 'npm error Missing script: "tests"\n'))
        self.assertIsNone(call("npm test", "1 failed, 2 passed\n"))

    def test_codex_wrong_form_then_failing_run_nudges_with_exit_codes(self):
        transcript = os.path.join(self.tmp, "rollout.jsonl")
        with open(transcript, "w") as handle:
            for call_id in ("exec-1", "exec-2"):
                item = {"type": "CommandExecution", "id": call_id, "exit_code": 1}
                handle.write(json.dumps({"type": "event_msg", "payload": {"type": "item_completed", "item": item}}) + "\n")
        def call(call_id, command, output):
            value = self.payload("codex", "PostToolUse", transcript_path=transcript, tool_use_id=call_id, tool_response=output)
            value["tool_input"] = {"command": command}
            return self.run_hook("codex", "PostToolUse", value)
        self.assertIsNone(call("exec-1", "npm run tests", 'npm error Missing script: "tests"\n'))
        self.assertIn("npm failed", self.context(call("exec-2", "npm test", "1 failing\n")))

    def test_program_missing_file_is_not_a_wrong_form(self):
        value = self.payload("claude", "PostToolUseFailure",
                             error="Exit code 1\nFileNotFoundError: [Errno 2] No such file or directory: 'data.csv'")
        value["tool_input"] = {"command": "python3 scripts/gen.py"}
        self.run_hook("claude", "PostToolUseFailure", value)
        value = self.payload("claude", "PostToolUseFailure", error="Exit code 1\nAssertionError")
        value["tool_input"] = {"command": "python3 scripts/other.py"}
        self.assertIsNone(self.run_hook("claude", "PostToolUseFailure", value))

    def test_missing_script_from_the_shell_is_a_wrong_form(self):
        for error in ("bash: scripts/test.sh: No such file or directory", "dash: 0: cannot open scripts/test.sh: No such file",
                      "(eval):1: no such file or directory: scripts/test.sh"):
            self.session = "s-" + str(abs(hash(error)))
            value = self.payload("claude", "PostToolUseFailure", error="Exit code 127\n" + error)
            value["tool_input"] = {"command": "bash scripts/test.sh"}
            self.run_hook("claude", "PostToolUseFailure", value)
            value = self.payload("claude", "PostToolUseFailure", error="Exit code 1\nAssertionError")
            value["tool_input"] = {"command": "bash scripts/run-tests.sh"}
            self.assertIsNotNone(self.run_hook("claude", "PostToolUseFailure", value), error)

    def test_flag_only_changes_are_the_same_command(self):
        self.shell("pytest tests/test_a.py", ok=False)
        self.assertIsNone(self.shell("pytest tests/test_a.py -q"))

    def test_outdated_doc_request_is_not_a_correction(self):
        self.assertIsNone(self.prompt("The CHANGELOG entry for 2.1 is outdated, please refresh it"))

class LimitsTest(HookTest):
    def test_u9_cap(self):
        nudges = 0
        for program in ("npm", "make", "cargo", "pytest", "go"):
            self.shell(program + " wrong", ok=False)
            nudges += 1 if self.shell(program + " right") else 0
        nudges += 1 if self.prompt("I told you to use pnpm") else 0
        self.assertEqual(nudges, 3)

    def test_u10_end_and_stale(self):
        self.shell("npm run tests", ok=False)
        self.assertEqual(len(self.state_files()), 1)
        self.run_hook("claude", "SessionEnd", self.payload("claude", "SessionEnd"))
        self.assertEqual(self.state_files(), [])
        self.shell("npm run tests", ok=False)
        old = time.time() - 2 * 86400
        for path in self.state_files():
            os.utime(path, (old, old))
        other = self.payload("claude", "SessionStart", session_id="another", cwd=self.tmp)
        self.run_hook("claude", "SessionStart", other)
        self.assertEqual(self.state_files(), [])

    def test_u11_bad_input(self):
        for raw in ("", "not json", "[]", "null", '{"tool_input": 5}', "\udcff"):
            for event in ("SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "SessionEnd", "Unknown"):
                self.run_hook("claude", event, None, raw=raw.encode("utf-8", "surrogateescape").decode("utf-8", "replace"))
        big = self.payload("claude", "PostToolUse")
        big["tool_response"] = {"stdout": "x" * 5000000}
        self.run_hook("claude", "PostToolUse", big)
        for ci in ("true", "false", "", "build-123"):
            self.assertIsNone(self.run_hook("claude", "SessionStart", self.payload("claude", "SessionStart"), env={"CI": ci}))

    def test_u11_read_only_data_folder(self):
        os.makedirs(self.data)
        os.chmod(self.data, stat.S_IRUSR | stat.S_IXUSR)
        try:
            self.assertIsNone(self.shell("npm run tests", ok=False))
            self.assertIsNotNone(self.run_hook("claude", "SessionStart", self.payload("claude", "SessionStart")))
        finally:
            os.chmod(self.data, stat.S_IRWXU)

    def test_u11_concurrent_writes(self):
        def fire(i):
            # Eight hooks starting at once can take seconds on a small runner; the 10 s timeout still catches a hang.
            return self.shell("tool%d run" % i, ok=False, budget=10)
        # Contended hooks deliberately drop events after 50 ms. CPU scheduling and
        # coverage tracing affect throughput, so assert atomicity and recovery instead
        # of imposing a hardware-dependent minimum number of accepted events.
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(fire, range(16)))
        (path,) = self.state_files()
        with open(path) as handle:
            state = json.load(handle)
        self.assertGreaterEqual(state["calls"], 1)
        self.assertLessEqual(state["calls"], 16)
        self.assertEqual(state["calls"], len(state["failures"]))
        before = state["calls"]
        self.shell("finaltool run", ok=False)
        with open(path) as handle:
            after = json.load(handle)
        self.assertEqual(after["calls"], before + 1)
        self.assertEqual(after["calls"], len(after["failures"]))
        self.assertTrue(set(state["failures"]) <= set(after["failures"]))

    def test_u12_state_contents_and_mode(self):
        self.prompt("I told you, the secret path is /srv/alice/private")
        self.shell("npm run tests --token=abc123", ok=False)
        path = self.state_files()[0]
        with open(path) as handle:
            text = handle.read()
        for leaked in ("alice", "abc123", "secret", "private"):
            self.assertNotIn(leaked, text)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)

    def test_u13_missing_python_wrapper(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as handle:
            command = json.load(handle)["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        command = command.replace("${CLAUDE_PLUGIN_ROOT}", ROOT)
        empty = os.path.join(self.tmp, "empty-bin")
        os.makedirs(empty)
        result = subprocess.run(["/bin/sh", "-c", command], input="{}", env={"PATH": empty, "HOME": self.home},
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))


class OpenCodeTest(HookTest):
    def oc_shell(self, fixture_name, command=None, **changes):
        captured = fixture("opencode", fixture_name)
        payload = {"session_id": self.session, "tool_name": "Bash", "tool_input": captured["input"],
                   "tool_response": "\n".join(p["text"] for p in captured["result"]["content"]),
                   "exit_code": captured["result"]["metadata"]["exit"]}
        if command:
            payload["tool_input"] = {"command": command}
        payload.update(changes)
        return self.run_hook("opencode", "PostToolUse", payload)

    def test_silent_failure_is_not_a_wrong_form(self):
        self.assertIsNone(self.oc_shell("shell-failure"))
        self.assertIsNone(self.oc_shell("shell-success"))

    def test_explicit_status_with_wrong_form_output(self):
        self.assertIsNone(self.oc_shell("shell-wrong"))
        self.assertIn("node failed", self.context(self.oc_shell("shell-success")))

    def test_missing_boolean_and_interrupted_status_are_unknown(self):
        for changes in ({"exit_code": None}, {"exit_code": True}, {"exit_code": "0"}, {"interrupted": True}):
            self.session += "x"
            self.oc_shell("shell-wrong")
            self.assertIsNone(self.oc_shell("shell-success", **changes))
            self.assertIn("node failed", self.context(self.oc_shell("shell-success")))

    def test_prompt_admission_retries_keep_only_id_hash(self):
        payload = {"session_id": self.session, "message_id": "private-message-id", "prompt": "I told you, private correction"}
        self.assertIn("corrected you", self.context(self.run_hook("opencode", "UserPromptSubmit", payload)))
        self.assertIsNone(self.run_hook("opencode", "UserPromptSubmit", payload))
        with open(self.state_files()[0]) as handle:
            raw = handle.read()
        self.assertNotIn("private", raw)
        self.assertEqual(json.loads(raw)["prompts"], 1)
        output = self.run_hook("opencode", "SessionStart", {"session_id": self.session, "correction_context": True})
        self.assertIn("corrected you", self.context(output))

    def test_replayed_tool_call_does_not_replace_failed_command(self):
        self.oc_shell("shell-wrong", call_id="tool-1")
        self.assertIsNone(self.oc_shell("shell-success", call_id="tool-1"))
        self.assertIn("node failed", self.context(self.oc_shell("shell-success", call_id="tool-2")))


if __name__ == "__main__":
    unittest.main()

import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("protection", os.path.join(ROOT, ".github", "scripts", "protect_branch.py"))
protection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protection)


class ProtectionTest(unittest.TestCase):
    def test_requires_actions_check_and_a_current_branch(self):
        policy = protection.required_checks(None)
        self.assertTrue(policy["strict"])
        self.assertEqual(policy["checks"], [{"context": c, "app_id": 15368} for c in protection.CHECKS])

    def test_preserves_existing_checks_and_pins_ci_jobs_to_actions(self):
        job = protection.CHECKS[0]
        original = {"strict": False, "contexts": ["legacy", "security", job],
                    "checks": [{"context": "security", "app_id": 42}, {"context": job, "app_id": None}]}
        policy = protection.required_checks(original)
        self.assertEqual({c["context"]: c["app_id"] for c in policy["checks"]},
                         dict({"legacy": None, "security": 42}, **{c: 15368 for c in protection.CHECKS}))
        self.assertFalse(original["strict"])

    def test_api_handles_github_errors_and_empty_responses(self):
        from types import SimpleNamespace
        from unittest import mock
        cases = [(0, '{"ok":true}', "", {"ok": True}), (0, "", "", None),
                 (1, "", "not found (HTTP 404)", None)]
        for code, out, err, expected in cases:
            with mock.patch.object(protection.subprocess, "run", return_value=SimpleNamespace(returncode=code, stdout=out, stderr=err)):
                self.assertEqual(protection.api("endpoint"), expected)
        with mock.patch.object(protection.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="Upgrade (HTTP 403)")):
            with self.assertRaisesRegex(SystemExit, "Upgrade"):
                protection.api("endpoint")

    def test_apply_preserves_existing_protection_and_verifies_ci_checks(self):
        import contextlib
        import io
        from unittest import mock
        existing = {"required_status_checks": {"contexts": ["security"], "checks": []}}
        confirmed = {"required_status_checks": protection.required_checks(existing["required_status_checks"]),
                     "enforce_admins": {"enabled": True}}
        for current in (None, existing):
            with mock.patch.object(sys, "argv", ["protect", "--apply"]), \
                    mock.patch.object(protection.subprocess, "check_output", return_value='{"nameWithOwner":"owner/repo","defaultBranchRef":{"name":"main"}}'), \
                    mock.patch.object(protection, "api", side_effect=[current, None, confirmed] if current is None else [current, None, None, confirmed]) as api, \
                    contextlib.redirect_stdout(io.StringIO()):
                protection.main()
                writes = [c for c in api.call_args_list if len(c.args) > 1]
                self.assertEqual(writes[0].args[1], "PUT" if current is None else "PATCH")
                if current:
                    self.assertIn({"context": "security", "app_id": None}, writes[0].args[2]["checks"])

    def test_dry_run_and_unconfirmed_write(self):
        import contextlib
        import io
        import sys
        from unittest import mock
        repo = '{"nameWithOwner":"owner/repo","defaultBranchRef":{"name":"main"}}'
        with mock.patch.object(sys, "argv", ["protect", "--repo", "owner/repo"]), \
                mock.patch.object(protection.subprocess, "check_output", return_value=repo) as view, \
                mock.patch.object(protection, "api", return_value=None) as api, contextlib.redirect_stdout(io.StringIO()):
            protection.main()
            self.assertEqual(api.call_count, 1)
            command = view.call_args.args[0]
            self.assertIn("owner/repo", command)
            self.assertNotIn("--repo", command)
        with mock.patch.object(sys, "argv", ["protect", "--apply"]), \
                mock.patch.object(protection.subprocess, "check_output", return_value=repo), \
                mock.patch.object(protection, "api", return_value=None), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "did not confirm"):
                protection.main()

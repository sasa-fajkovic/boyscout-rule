"""Check the credential-free CI contract; live harness tests are local-only."""
import copy
import importlib.util
import json
import os
import re
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_ONLY = ("tests/wiring.py", "tests/behavior.py", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
             "@anthropic-ai/claude-code", "@openai/codex", "@opencode/cli")


def validate_local_only(workflow):
    content = json.dumps(workflow)
    assert not any(token in content for token in LIVE_ONLY), "live harness execution or provider credentials in Actions"


def validate(workflow):
    jobs = workflow["jobs"]
    assert set(jobs) == {"test", "javascript"}
    assert workflow["permissions"] == {"contents": "read"}
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 treats "on" as a boolean.
    assert "pull_request_target" not in triggers
    assert {"pull_request", "push"} <= set(triggers)
    for job in jobs.values():
        assert not job.get("continue-on-error")
        assert job.get("timeout-minutes", 0) > 0
        for step in job.get("steps", []):
            assert not step.get("continue-on-error")
            if "uses" in step:
                assert re.search(r"@[a-f0-9]{40}$", step["uses"]), step["uses"]
                if step["uses"].startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] is False
    validate_local_only(workflow)


def check_names(workflow):
    names = set()
    for job in workflow["jobs"].values():
        for entry in job.get("strategy", {}).get("matrix", {}).get("include") or [{}]:
            names.add(re.sub(r"\$\{\{ matrix\.(\w+) \}\}", lambda m: str(entry[m.group(1)]), job["name"]))
    return names


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, ".github", "workflows", "ci.yml")) as handle:
            self.workflow = yaml.safe_load(handle)

    def test_shipped_workflow(self):
        validate(self.workflow)

    def test_no_workflow_launches_live_harness_tests(self):
        folder = os.path.join(ROOT, ".github", "workflows")
        for filename in os.listdir(folder):
            if filename.endswith((".yml", ".yaml")):
                with self.subTest(workflow=filename), open(os.path.join(folder, filename)) as handle:
                    validate_local_only(yaml.safe_load(handle))

    def test_branch_protection_requires_every_ci_job(self):
        spec = importlib.util.spec_from_file_location("protection", os.path.join(ROOT, ".github", "scripts", "protect_branch.py"))
        protection = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(protection)
        self.assertEqual(set(protection.CHECKS), check_names(self.workflow))

    def test_rejects_ignored_failures(self):
        for change in (lambda w: w["jobs"]["javascript"].update({"continue-on-error": True}),
                       lambda w: w["jobs"]["javascript"]["steps"][0].update({"continue-on-error": True})):
            workflow = copy.deepcopy(self.workflow)
            change(workflow)
            with self.assertRaises(AssertionError):
                validate(workflow)

    def test_rejects_live_tests_even_inside_an_existing_job(self):
        for token in LIVE_ONLY:
            workflow = copy.deepcopy(self.workflow)
            workflow["jobs"]["test"]["steps"].append({"run": token})
            with self.subTest(token=token), self.assertRaises(AssertionError):
                validate(workflow)
        workflow = copy.deepcopy(self.workflow)
        workflow["jobs"]["e2e"] = {"timeout-minutes": 60, "steps": []}
        with self.assertRaises(AssertionError):
            validate(workflow)


if __name__ == "__main__":
    unittest.main()

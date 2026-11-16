import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, ".github", "scripts", "release.py")
PYTHON = os.environ.get("BOYSCOUT_TEST_PYTHON", sys.executable)
MANIFESTS = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "package.json")
FAKE_GH = """#!/bin/sh
dir="@DIR@"
case "$1 $2" in
  "api "*) sha=$(echo "$2" | sed 's#.*/commits/##; s#/pulls##'); cat "$dir/pulls/$sha.json" 2>/dev/null || echo '[]' ;;
  "release view") [ -f "$dir/releases/$3" ] ;;
  "release create") shift 2; printf '%s\\n' "$@" > "$dir/releases/$1" ;;
  "pr list") [ ! -f "$dir/pr" ] && echo '[]' || echo '[{"url":"https://example.test/pr/1"}]' ;;
  "pr create") cat > "$dir/pr" ;;
  "workflow run") printf '%s\\n' "$@" > "$dir/dispatch" ;;
  *) exit 1 ;;
esac
"""


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origin = os.path.join(self.tmp, "origin.git")
        self.repo = os.path.join(self.tmp, "repo")
        self.gh = os.path.join(self.tmp, "gh")
        for folder in ("bin", "gh/pulls", "gh/releases"):
            os.makedirs(os.path.join(self.tmp, folder))
        with open(os.path.join(self.tmp, "bin", "gh"), "w") as handle:
            handle.write(FAKE_GH.replace("@DIR@", self.gh))
        os.chmod(os.path.join(self.tmp, "bin", "gh"), 0o755)
        self.env = dict(os.environ, PATH=os.path.join(self.tmp, "bin") + os.pathsep + os.environ["PATH"],
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        if os.environ.get("BOYSCOUT_COVERAGE") == "1":
            self.env["COVERAGE_FILE"] = os.path.join(ROOT, ".coverage")
        self.git("init", "-q", "--bare", "-b", "main", self.origin, cwd=self.tmp)
        self.git("init", "-q", "-b", "main", self.repo, cwd=self.tmp)
        for path in MANIFESTS:
            os.makedirs(os.path.dirname(os.path.join(self.repo, path)), exist_ok=True)
            data = {"name": "boyscout-rule", "version": "0.3.6"}
            with open(os.path.join(self.repo, path), "w") as handle:
                json.dump(data, handle)
        self.git("add", "-A")
        self.git("commit", "-qm", "start")
        self.git("tag", "v0.3.6")
        self.git("remote", "add", "origin", self.origin)
        self.git("push", "-q", "origin", "main", "v0.3.6")
        open(os.path.join(self.gh, "releases", "v0.3.6"), "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args, cwd=None):
        return subprocess.run(("git",) + args, cwd=cwd or self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def merge(self, number, labels=(), title=None):
        with open(os.path.join(self.repo, "change%d.txt" % number), "w") as handle:
            handle.write("%d\n" % number)
        self.git("add", "-A")
        self.git("commit", "-qm", "change %d" % number)
        sha = self.git("rev-parse", "HEAD")
        self.record_pr(sha, number, title or "Change %d" % number, labels)
        self.git("push", "-q", "origin", "main")
        return sha

    def record_pr(self, sha, number, title, labels=()):
        pr = {"number": number, "title": title, "merged_at": "2026-11-02T00:00:00Z",
              "merge_commit_sha": sha, "labels": [{"name": name} for name in labels]}
        with open(os.path.join(self.gh, "pulls", sha + ".json"), "w") as handle:
            json.dump([pr], handle)

    def run_release(self, sha=None):
        sha = sha or self.git("rev-parse", "HEAD")
        runner = [PYTHON]
        if os.environ.get("BOYSCOUT_COVERAGE") == "1":
            runner += ["-m", "coverage", "run", "--parallel-mode", "--rcfile=" + os.path.join(ROOT, ".coveragerc")]
        return subprocess.run(runner + [SCRIPT, sha, "example/demo"], cwd=self.repo, env=self.env,
                              capture_output=True, text=True)

    def versions(self, ref="main"):
        return {json.loads(self.git("show", "%s:%s" % (ref, path), cwd=self.origin))["version"] for path in MANIFESTS}

    def finish(self, version):
        self.git("switch", "main")
        self.git("merge", "--ff-only", "release/v" + version)
        self.record_pr(self.git("rev-parse", "HEAD"), 100, "Release v" + version)
        self.git("push", "-q", "origin", "main")
        return self.run_release()

    def assertPrepared(self, result, version):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.versions(), {"0.3.6"}, "release preparation must never change origin/main")
        self.assertEqual(self.versions("release/v" + version), {version})
        self.assertEqual(self.git("tag", cwd=self.origin), "v0.3.6")
        with open(os.path.join(self.gh, "dispatch")) as handle:
            self.assertIn("release/v" + version, handle.read())

    def test_minor_label_and_tested_release_merge(self):
        self.merge(18, ["minor"], "Rewrite")
        self.assertPrepared(self.run_release(), "0.4.0")
        result = self.finish("0.4.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.versions(), {"0.4.0"})
        self.assertEqual(self.git("rev-parse", "v0.4.0", cwd=self.origin), self.git("rev-parse", "main", cwd=self.origin))
        with open(os.path.join(self.gh, "releases", "v0.4.0")) as handle:
            self.assertIn("- Rewrite (#18)", handle.read())

    def test_unlabeled_is_patch(self):
        self.merge(19)
        self.assertPrepared(self.run_release(), "0.3.7")

    def test_highest_label_wins(self):
        self.merge(19, ["patch"])
        self.merge(20, ["major", "patch"])
        self.assertPrepared(self.run_release(), "1.0.0")
        with open(os.path.join(self.gh, "pr")) as handle:
            self.assertIn("- Change 19 (#19)\n- Change 20 (#20)", handle.read())

    def test_retry_does_not_push_another_release_branch(self):
        self.merge(19)
        self.assertEqual(self.run_release().returncode, 0)
        before = self.git("rev-parse", "release/v0.3.7", cwd=self.origin)
        self.git("switch", "main")
        self.assertEqual(self.run_release().returncode, 0)
        self.assertEqual(before, self.git("rev-parse", "release/v0.3.7", cwd=self.origin))
        self.assertEqual(self.versions(), {"0.3.6"})

    def test_tagged_release_is_not_released_again(self):
        self.merge(19)
        self.assertEqual(self.run_release().returncode, 0)
        self.assertEqual(self.finish("0.3.7").returncode, 0)
        result = self.run_release()
        self.assertIn("nothing to release since v0.3.7", result.stdout)

    def test_superseded_run_does_nothing(self):
        older = self.merge(19)
        self.merge(20)
        result = self.run_release(older)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("main has moved past", result.stdout)
        self.assertEqual(self.versions(), {"0.3.6"})

    def test_direct_commit_without_pr_does_not_release(self):
        with open(os.path.join(self.repo, "direct.txt"), "w") as handle:
            handle.write("x\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "direct")
        self.assertIn("nothing to release", self.run_release().stdout)
        self.assertEqual(self.git("tag", cwd=self.origin), "v0.3.6")

    def test_missing_release_recovered(self):
        self.merge(19)
        self.run_release()
        self.finish("0.3.7")
        os.remove(os.path.join(self.gh, "releases", "v0.3.7"))
        result = self.run_release()
        self.assertIn("published missing release v0.3.7", result.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.gh, "releases", "v0.3.7")))

    def test_rejected_push_publishes_nothing(self):
        self.merge(19)
        with open(os.path.join(self.origin, "hooks", "pre-receive"), "w") as handle:
            handle.write("#!/bin/sh\nexit 1\n")
        os.chmod(os.path.join(self.origin, "hooks", "pre-receive"), 0o755)
        self.assertNotEqual(self.run_release().returncode, 0)
        self.assertEqual(self.versions(), {"0.3.6"})
        self.assertEqual(self.git("tag", cwd=self.origin), "v0.3.6")
        self.assertFalse(os.path.exists(os.path.join(self.gh, "pr")))


if __name__ == "__main__":
    unittest.main()

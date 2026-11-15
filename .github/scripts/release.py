"""Release tested main through a version PR, then tag the tested merge and publish a GitHub Release.

Usage: release.py <sha CI passed> <owner/repo>, run from a full clone with main checked out and GH_TOKEN set.
"""
import json
import os
import re
import subprocess
import sys

MANIFESTS = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "package.json", "package-lock.json")
LEVELS = ("patch", "minor", "major")
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
BOT = {"GIT_%s_%s" % (role, field): value for role in ("AUTHOR", "COMMITTER")
       for field, value in (("NAME", "github-actions[bot]"), ("EMAIL", "41898282+github-actions[bot]@users.noreply.github.com"))}


def run(*args, env=None):
    return subprocess.run(args, check=True, capture_output=True, text=True, env=env).stdout.strip()


def parse(tag):
    return tuple(int(part) for part in TAG.match(tag).groups())


def bump(version, level):
    major, minor, patch = version
    if level == "major":
        return major + 1, 0, 0
    if level == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def level_of(prs):
    """The highest version label among the PRs; a PR without one counts as patch."""
    found = [LEVELS.index(label["name"]) for pr in prs for label in pr.get("labels", []) if label["name"] in LEVELS]
    return LEVELS[max(found, default=0)]


def latest_tag(ref="HEAD"):
    tags = [tag for tag in run("git", "tag", "--merged", ref).splitlines() if TAG.match(tag)]
    return max(tags, key=parse, default=None)


def merged_prs(repo, start, end):
    """PRs whose merge landed on main's first-parent line in start..end, oldest first."""
    span = "%s..%s" % (start, end) if start else end
    prs = []
    for sha in reversed(run("git", "rev-list", "--first-parent", span).splitlines()):
        for pr in json.loads(run("gh", "api", "repos/%s/commits/%s/pulls" % (repo, sha)) or "[]"):
            if pr.get("merged_at") and pr.get("merge_commit_sha") == sha:
                prs.append(pr)
    return prs


def notes(prs):
    return "\n".join("- %s (#%d)" % (pr["title"], pr["number"]) for pr in prs) or "Maintenance release."


def ensure_release(repo, tag):
    """Finish a run that pushed its tag but failed before the GitHub Release existed."""
    if subprocess.run(["gh", "release", "view", tag, "--repo", repo], capture_output=True).returncode == 0:
        return
    previous = latest_tag(tag + "^")
    run("gh", "release", "create", tag, "--repo", repo, "--verify-tag", "--title", tag, "--notes", notes(merged_prs(repo, previous, tag)))
    print("published missing release " + tag)


def write_version(version):
    for path in MANIFESTS:
        with open(path) as handle:
            data = json.load(handle)
        data["version"] = version
        if path == "package-lock.json":
            data["packages"][""]["version"] = version
        with open(path, "w") as handle:
            handle.write(json.dumps(data, indent=2) + "\n")


def prepare(repo, version, prs):
    branch = "release/v" + version
    remote = run("git", "ls-remote", "--heads", "origin", "refs/heads/" + branch)
    if not remote:
        run("git", "switch", "--create", branch)
        write_version(version)
        run("git", "add", "--", *MANIFESTS)
        run("git", "commit", "-m", "Release v" + version, env=dict(os.environ, **BOT))
        run("git", "push", "--no-follow-tags", "origin", "refs/heads/" + branch + ":refs/heads/" + branch)
    existing = json.loads(run("gh", "pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--json", "url"))
    if not existing:
        body = "Update plugin and npm manifests together. Merge only after every CI job passes.\n\n" + notes(prs)
        subprocess.run(["gh", "pr", "create", "--repo", repo, "--base", "main", "--head", branch,
                        "--title", "Release v" + version, "--body-file", "-"], input=body, text=True, check=True)
    # GITHUB_TOKEN pushes do not trigger PR workflows; dispatch CI explicitly on this exact branch.
    run("gh", "workflow", "run", "ci.yml", "--repo", repo, "--ref", branch)
    print("awaiting tested release PR " + branch)


def main(argv):
    passed, repo = argv[1], argv[2]
    tag = latest_tag()
    if tag:
        ensure_release(repo, tag)
    if run("git", "rev-parse", "HEAD") != run("git", "rev-parse", passed + "^{commit}"):
        print("main has moved past %s; the run for the newer commit releases it" % passed)
        return 0
    prs = merged_prs(repo, tag, "HEAD")
    if not prs:
        print("nothing to release since " + str(tag))
        return 0
    version = "%d.%d.%d" % bump(parse(tag) if tag else (0, 0, 0), level_of(prs))
    new = "v" + version
    with open(MANIFESTS[0]) as handle:
        current = json.load(handle)["version"]
    if current != version:
        prepare(repo, version, prs)
        return 0
    for path in MANIFESTS:
        with open(path) as handle:
            if json.load(handle)["version"] != version:
                raise SystemExit("manifest versions disagree: " + path)
    run("git", "tag", new)
    run("git", "push", "--no-follow-tags", "origin", "refs/tags/" + new)
    run("gh", "release", "create", new, "--repo", repo, "--verify-tag", "--title", new, "--notes", notes(prs))
    print("released " + new)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

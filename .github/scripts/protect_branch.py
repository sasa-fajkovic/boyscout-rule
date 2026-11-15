"""Require every CI job check, preserving existing branch protection.

Usage: python3 .github/scripts/protect_branch.py [--repo owner/name] [--apply]
Without --apply, only show the intended policy. Requires GitHub Pro for private personal repositories.
"""
import argparse
import json
import subprocess

CHECKS = ("Python 3.9 / ubuntu-22.04", "Python 3.14 / ubuntu-latest", "OpenCode adapter and package")
ACTIONS_APP = 15368


def required_checks(current):
    checks = {item["context"]: {"context": item["context"], "app_id": item.get("app_id")}
              for item in (current or {}).get("checks", [])}
    for context in (current or {}).get("contexts", []):
        checks.setdefault(context, {"context": context, "app_id": None})
    for check in CHECKS:
        checks[check] = {"context": check, "app_id": ACTIONS_APP}
    return {"strict": True, "checks": list(checks.values())}


def api(endpoint, method="GET", body=None):
    args = ["gh", "api", endpoint, "--method", method]
    if body is not None:
        args += ["--input", "-"]
    result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                            capture_output=True, text=True)
    if result.returncode:
        if method == "GET" and "(HTTP 404)" in result.stderr:
            return None
        raise SystemExit(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    command = ["gh", "repo", "view"]
    if args.repo:
        command.append(args.repo)
    command += ["--json", "nameWithOwner,defaultBranchRef"]
    repo = json.loads(subprocess.check_output(command, text=True))
    endpoint = "repos/%s/branches/%s/protection" % (repo["nameWithOwner"], repo["defaultBranchRef"]["name"])
    current = api(endpoint)
    checks = required_checks((current or {}).get("required_status_checks"))
    if not args.apply:
        print(json.dumps({"required_status_checks": checks, "enforce_admins": True}, indent=2))
        return
    if current is None:
        api(endpoint, "PUT", {"required_status_checks": checks, "enforce_admins": True,
                              "required_pull_request_reviews": {"required_approving_review_count": 0},
                              "restrictions": None, "allow_force_pushes": False, "allow_deletions": False})
    else:
        api(endpoint + "/required_status_checks", "PATCH", checks)
        api(endpoint + "/enforce_admins", "POST")
    result = api(endpoint)
    if (not result or not result["required_status_checks"]["strict"] or not result["enforce_admins"]["enabled"]
            or any({"context": check, "app_id": ACTIONS_APP} not in result["required_status_checks"]["checks"]
                   for check in CHECKS)):
        raise SystemExit("GitHub did not confirm the required CI policy")
    print("Verified: %s required on %s, including for administrators." % (", ".join(CHECKS), repo["defaultBranchRef"]["name"]))


if __name__ == "__main__":
    main()

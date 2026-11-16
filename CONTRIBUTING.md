# Contributing

## Layout

| Path | Purpose |
|---|---|
| `skills/boyscout-rule/SKILL.md` | The skill, shared by every harness |
| `hooks/boyscout.py` | Shared Python engine; `boyscout.py <harness> <event>` |
| `hooks/hooks.json`, `hooks/codex.json` | Hook registration for Claude Code and Codex |
| `.claude-plugin/`, `.codex-plugin/` | Plugin and marketplace manifests |
| `opencode/`, `package.json` | OpenCode 2 adapter and distributable; no runtime npm dependencies |
| `tests/` | Static checks, unit tests, harness tests and fixtures |
| `tests/package.json`, `tests/package-lock.json` | JavaScript development dependencies. They stay out of the repository root because Claude Code runs an npm install inside an installed plugin whose root has both `package.json` and a lockfile |
| `.github/workflows/`, `.github/scripts/` | CI, releases and branch protection |

## Tests

```sh
python3 -m pip install -r tests/requirements.txt
npm ci --ignore-scripts --prefix tests
python3 tests/static_checks.py
/usr/bin/python3 tests/static_checks.py
python3 -m unittest discover -s tests/unit
BOYSCOUT_TEST_PYTHON=/usr/bin/python3 /usr/bin/python3 -m unittest discover -s tests/unit
npm run typecheck --prefix tests
npm test --prefix tests
python3 tests/package_check.py
python3 tests/ci_checks.py
actionlint
```

These checks need no provider credentials. Use a virtual environment for the Python development dependencies. CI covers Python 3.9 and 3.14 on Linux only; macOS runners are too expensive, so run the `/usr/bin/python3` commands above locally on a Mac before merging hook changes. It also checks the JavaScript adapter against the OpenCode SDK types, the actual npm tarball, workflow syntax and dependency advisories.

Live E2E tests run manually on a local machine, using installed Claude Code, Codex and OpenCode 2 CLIs. They can consume your configured provider's subscription allowance or API credits. They load the plugin for the session only and use synthetic repositories with a local origin and a fake `gh`, so nothing reaches GitHub:

```sh
python3 tests/wiring.py claude codex opencode
python3 tests/behavior.py --harness claude,codex,opencode --runs 1 --jobs 2
python3 tests/behavior.py --harness claude B1 # one scenario on one harness
```

Codex tests install the real marketplace/plugin inside a temporary `CODEX_HOME` and trust its four shipped hooks by their current hashes. This covers plugin loading, skill discovery and `${CLAUDE_PLUGIN_ROOT}` expansion. Local tests share your existing Codex login and copy top-level settings; nothing is installed in your personal configuration. OpenCode tests use a private server and temporary home, config, data, state and cache directories. They set `PWD` to the synthetic repo so OpenCode cannot inherit the caller's working directory.

The production plugin intentionally does nothing when `CI` is set. The live-test entrypoints also refuse to launch when `CI` or `GITHUB_ACTIONS` is set. Never remove those guards. Isolated test sessions omit GitHub credentials from their environment. Wiring tests require both captured context and a model response quoting the reminders, and fail on nonzero exits or empty responses. Behavior tests inspect the local origin, all published patches and messages, the fake GitHub log, user changes and worktree cleanup. Harness runs use isolated process groups: completion or timeout terminates remaining children before deleting the synthetic repository. Timeouts fail with exit code 124; artifacts retain partial output, standard error and the exit status for diagnosis.

GitHub Actions runs only the credential-free checks above. It does not install agent CLIs, call model providers or need model API secrets. Fork PRs run the same checks. Each CI job is its own required check, covering Python, JavaScript, package validation and workflow checks; local live E2E results are reported separately in the PR before merge when hooks or the skill change.

Local prerequisites are `git`, `node`, `jq` and the CLIs you select. Claude Code and Codex use your local login. The OpenCode test environment is isolated and uses `opencode/big-pickle` by default; free-provider quotas can limit a full run. Optional local environment variables `BOYSCOUT_CLAUDE_MODEL`, `BOYSCOUT_CODEX_MODEL` and `BOYSCOUT_OPENCODE_MODEL` choose models for the harness runners; `tests/behavior.py --model` overrides the behavior-test model. Configure credentials locally if the selected provider needs them. Live failures are not retried into a passing result.

For subprocess branch coverage:

```sh
BOYSCOUT_COVERAGE=1 python3 -m coverage run --parallel-mode -m unittest discover -s tests/unit
python3 -m coverage combine --quiet
python3 -m coverage report
```

The 90% threshold includes the shared engine and release automation.
Results and transcripts go to `tests/out/`. `tests/probe/` holds the probe plugin used to capture real hook inputs into `tests/fixtures/`.

## Pull requests

Keep changes small. Describe behavior, what you tested and what remains untested. Check the diff for credentials and private material before pushing.

## Releases

After CI passes on `main`, the release workflow opens a `release/vX.Y.Z` PR updating both plugin manifests and `package.json`. It explicitly dispatches CI on that branch because pushes made with `GITHUB_TOKEN` do not trigger PR workflows. Merge that PR after every CI job passes; CI on the resulting `main` commit then tags that tested commit and publishes the GitHub Release. The release job never pushes a commit directly to `main`. Enable “Allow GitHub Actions to create and approve pull requests” in Actions settings so it can create release PRs. Installed plugins only update when their manifest version changes, so never edit versions by hand.
| PR label | From `0.4.2` |
|---|---|
| `major` | `1.0.0` |
| `minor` | `0.5.0` |
| `patch`, or none | `0.4.3` |

When several PRs land before a release runs, they ship together with the highest of their labels. Release preparation is idempotent; a failed dispatch can be retried. Commits pushed to `main` without a PR are not released on their own. If a release run fails, re-run it from the Actions tab.


## Required merge checks

A workflow alone does not enforce merges. Configure GitHub branch protection with:

```sh
python3 .github/scripts/protect_branch.py          # inspect the policy
python3 .github/scripts/protect_branch.py --apply  # apply and verify it
```

The script preserves existing required checks, requires a current branch and pins every CI job check to GitHub Actions. `tests/ci_checks.py` fails if its `CHECKS` list drifts from the job names in `ci.yml`; renamed or removed jobs stay required until you remove them from branch protection. It enforces protection for administrators too. New protection also prevents force pushes/deletion and requires a PR. Private personal repositories require GitHub Pro; the API refuses protection on Free. Keep the repository private until local harness validation and branch protection have been verified.

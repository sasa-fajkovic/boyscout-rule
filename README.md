# boyscout-rule

Leave the repository better than you found it.

A plugin for Claude Code, Codex and OpenCode 2. When an agent learns something about a repository (a stale doc, a wrong command, a small defect next to its work, or a procedure worth a repo skill), it fixes the source in a small draft PR instead of saving the lesson to memory. Every teammate's next session benefits, and you review each change like any other PR.

```text
session ── hooks notice ─────────────► skill decides ──► worktree ──► draft PR
           failed-then-fixed command      lesson / small fix / repo skill
           user correction                (fixes ≤ 20 lines, never your checkout)
           repeated command sequence
```

## Install

Requires `git`, an authenticated `gh` and `python3` (on macOS it comes with the developer tools that also provide `git`). Runs on macOS, Linux and WSL. The PRs are drafts, which GitHub [supports in public and private repositories on every plan](https://github.blog/changelog/2025-05-01-draft-pull-requests-are-now-available-in-all-repositories/). `origin` must be the GitHub repo that `gh` resolves for the checkout; in a fork with an `upstream` remote the agent reports its findings instead of publishing them. On native Windows the hooks need Git Bash, and only the session-start context works there; the other hooks stay off.

**Claude Code** (CLI and Desktop):

```sh
claude plugin marketplace add sasa-fajkovic/boyscout-rule
claude plugin install boyscout-rule@boyscout-rule
```

Then turn off auto memory: run `/memory` and switch it off. The plugin warns at session start while it is on.

**Codex** (CLI):

```sh
codex plugin marketplace add sasa-fajkovic/boyscout-rule
codex plugin add boyscout-rule@boyscout-rule
```

Then start `codex`, open `/hooks` and trust the four boyscout-rule hooks. Codex silently skips untrusted hooks, so this step is required; repeat it after each plugin update. Codex memories are off by default; the plugin warns if you turned them on.

In Codex's default sandbox, `git` writes and pushes need your approval. Approve them, or run with a mode that allows them, for fully autonomous PRs.

Claude Code asks before the publish commands (`git fetch`, `git worktree`, `git commit`, `git push`, `gh api`, `gh pr create`) and before edits in the temporary worktree outside your project, unless your permission rules already allow them. For fully unattended PRs, use auto mode.

**OpenCode 2** (tested with 2.1.3):

Clone the plugin to a permanent location:

```sh
git clone https://github.com/sasa-fajkovic/boyscout-rule.git ~/.local/share/boyscout-rule
```

Add the absolute path to its `opencode` directory to `plugins` in your OpenCode configuration (`~/.config/opencode/opencode.json`), preserving your other settings:

```json
{
  "plugins": ["/absolute/path/to/boyscout-rule/opencode"]
}
```

Restart OpenCode. The plugin registers the shared skill automatically. OpenCode 1 is not supported. The adapter uses the documented [OpenCode 2 plugin hooks](https://opencode.ai/v2/docs/build/plugins/server/), requires `python3`, and has no runtime npm dependencies. OpenCode can ask for permission to access the temporary worktree outside your project; allow that location to enable unattended publishing.

## What it does

| Part | When | Effect |
|---|---|---|
| Skill `boyscout-rule` | The agent finds something worth fixing | Decides lesson, small fix (≤ 20 lines) or repo skill; publishes it as a draft PR from a separate worktree; one PR per session, or a new one once that PR passes about 100 lines or is no longer an open draft |
| Session start | Every session | Tells the agent the plugin is active; warns once if native memory is on |
| Failed, then fixed | A command fails and a different command for the same program works within the next 5 commands, or runs at all after a wrong-command error such as a missing npm script (the program is the command's first word, so `./a.sh` and `./b.sh` are different programs); read-only commands such as `ls`, `cat`, `grep` and `git` do not count | Asks the agent to fix the doc or script that pointed to the failing form |
| User correction | You write "no, …", "I told you", "that's wrong", … | Asks the agent to fix the instruction that caused the mistake |
| Repeated procedure | The same sequence of 3 or more commands runs twice | Asks the agent to propose a repo skill or script |
| Session end | Claude Code/Codex session closes; OpenCode session is deleted | Deletes the session's counters (OpenCode retains them across idle turns and resumes; stale counters expire after a day) |

Hooks never block the agent and send at most three nudges per session. They keep only counters and hashes, never prompt text or command output. Nothing leaves your machine except the PRs the agent opens.

Installing the plugin is your standing permission for the agent to push `boyscout/*` branches and open draft PRs. It never pushes to the default branch, force-pushes, merges or closes PRs. Close a PR you don't want with `gh pr close <n> --delete-branch`.

The hooks do nothing when the `CI` environment variable is set.

## Update

Each merged PR is released as a new version ([releases](https://github.com/sasa-fajkovic/boyscout-rule/releases)). To update:

```sh
claude plugin marketplace update boyscout-rule
claude plugin update boyscout-rule@boyscout-rule
codex plugin marketplace upgrade boyscout-rule
git -C ~/.local/share/boyscout-rule pull --ff-only
```

Restart running sessions to load the new version. In Codex, trust the updated hooks again in `/hooks`.

## Uninstall

```sh
claude plugin uninstall boyscout-rule@boyscout-rule
codex plugin remove boyscout-rule@boyscout-rule
```

For OpenCode, remove the plugin path from `plugins` in your configuration and restart.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).

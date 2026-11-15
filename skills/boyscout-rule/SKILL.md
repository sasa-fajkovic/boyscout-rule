---
name: boyscout-rule
description: Use when work reveals something in the repository worth improving - an instruction, doc, script or config that caused a mistake or wasted effort, a small code defect next to your work, or a repo-specific multi-step procedure worth a skill. Publishes the improvement as a small draft PR instead of saving it to memory.
---

# Boy Scout Rule

Leave the repository better than you found it. What you learn goes into the repository, never into memory or a notes file.

## When to act

Act only on something you verified during this session:

- Lesson: a repo instruction, doc, script or config led you or the user astray (wrong command, missing step, stale path).
- Small fix: a self-contained defect next to your work, under about 20 changed lines. If the fix is bigger, tell the user in one line and open no PR.
- Skill: a repo-specific procedure of three or more steps that worked and will recur (you did it twice, or git history shows the same kind of change before) and that no existing skill covers.

Skip: personal preferences, machine-specific facts, guesses, things already documented, and the user's task itself. A defect the user's task is about, such as the failure they asked you to check or fix, belongs to the user: report it, never fix it in a PR. Skip everything when the `CI` environment variable is set.

## What to change

- Fix the file that caused the problem; prefer editing existing docs and skills over adding new ones.
- A deterministic procedure becomes a script plus one line of docs, not a skill.
- A new skill goes in the repo's existing skills folder (`.claude/skills/`, `.agents/skills/` or `.opencode/skills/`). If there is none, use the current harness's folder: `.claude/skills/` for Claude Code, `.agents/skills/` for Codex, `.opencode/skills/` for OpenCode.
- Make the change only in the worktree. Never apply it to the user's checkout as well, even when the user's task needed the corrected command; just use the correct command there.
- Never edit files outside the repository or memory files.

## How to publish

The user installed boyscout-rule to get these PRs without being asked each time: that is their standing permission to push `boyscout/*` branches and open draft PRs for findings that meet this skill's rules. It covers nothing else.

Always hand publishing to a sub-agent when the harness offers one, so the user's task keeps moving. If the harness lets you choose the sub-agent's model, choose the cheapest one it offers; otherwise leave the choice to the harness. Give it the session id, the default branch and this session's PR link if there is one, and tell it it is the publisher. If the harness has no sub-agents or spawning one fails, or you are the publisher, publish it yourself. Publish one finding at a time: never start a second publisher while one is still running.

1. Requirements: `gh repo view --json nameWithOwner,url,defaultBranchRef` works for this repo, and `<repo>` is the host of its `url` plus its `nameWithOwner` (for example `github.com/owner/name`); the fetch URL (`git remote get-url origin`) and every push URL (`git remote get-url --push --all origin`) point to that same GitHub repo; `GH_REPO` is unset or names it too; and `gh auth status --active --hostname <host of the url>` works (on a gh without `--active`, `gh auth status --hostname <host of the url>`). If any check fails, tell the user in one line and stop.
2. Duplicates: list every open PR with `gh api --paginate --hostname <host> "repos/<owner/name>/pulls?state=open&per_page=100" --jq '.[] | select(.head.ref | startswith("boyscout/")) | "\(.number) \(.head.ref) \(.html_url)"'`, and closed, unmerged ones with `gh pr list --repo <repo> --state closed --search "is:unmerged head:boyscout" --limit 500 --json number,headRefName,url` (GitHub search matches branch names by prefix and may lag a few minutes, which does not matter for rejections). Keep only PRs whose branch starts with `boyscout/`. If an open one already makes the same fix (check with `gh pr diff <number> --repo <repo>`), skip the finding and tell the user its link; if a closed, unmerged one made it, the user rejected it, so skip it. Never change another session's PR.
3. Branch: `<branch>` is `boyscout/<session>`, where `<session>` is the session id from the boyscout-rule context, or a short random id if there is none. For a later finding in the same session, reuse the branch only while its PR is an open draft (`gh pr view <branch> --repo <repo> --json state,isDraft,additions,deletions`) under about 100 changed lines and the branch still exists on origin; otherwise use the next free name: `boyscout/<session>-2`, `-3`, and so on.
4. Worktree: `git fetch origin +refs/heads/<default-branch>:refs/remotes/origin/<default-branch>`, then `git worktree add --no-track -b <branch> <worktree> origin/<default-branch>`, where `<worktree>` is `"${TMPDIR:-/tmp}/"` plus `<branch>` with `/` replaced by `-`. To reuse a branch, fetch it the same way and start the worktree from `origin/<branch>` instead. If a local branch named `<branch>` or the path already exists, leave them alone and use the next free name.
5. Work only inside the worktree for steps 5 to 7: run git as `git -C <worktree>` and edit files by their absolute path under `<worktree>`, because a `cd` in one shell command does not carry over to file-edit tools or, in some harnesses, to the next command. Confirm the problem also exists on the default branch. If it does not, or the fix depends on your unpublished task changes, skip it.
6. Change the files there. Run the repo's cheap checks for the touched files if they exist.
7. Commit with a message that states the problem and the fix. No merge commits and no binary files. Let the repo's commit hooks run; if they fail and the cause is not obvious, stop and tell the user.
8. Scan everything that will be published for secrets (keys, tokens, passwords) and for text from the conversation: every outgoing commit with its patch and message (`git log -p origin/<default-branch>..<branch>`), and the PR title and body you plan to use. Stop and tell the user if you find any, or if `git log --merges origin/<default-branch>..<branch>` lists a commit or `git diff --numstat origin/<default-branch>...<branch>` shows `-` for a file. Use the branch name, not `HEAD`, here and in step 9, so the commands publish the worktree's branch from any directory.
9. Push only that branch, by name and without tags: `git push --no-follow-tags origin refs/heads/<branch>:refs/heads/<branch>`, with the branch name written out, not through a shell variable (zsh reads `$B:r` as a modifier). If the push is rejected, stop, tell the user in one line and clean up; never fork the repo, add or change remotes, or push anywhere else.
10. New branch: `gh pr create --draft --repo <repo> --base <default-branch> --head <branch> --title <title> --body-file -`, with the body on standard input so no file is left behind. If `gh pr create` fails, check `gh pr view <branch> --repo <repo>`: only when its error says `no pull requests found for branch` (both that and other errors exit 1), delete the branch you just created and pushed (`git push --no-follow-tags origin --delete refs/heads/<branch>`; never any other branch); if a PR exists or the check fails, keep the branch. Either way clean up and tell the user in one line. Reused branch: update the PR description the same way with `gh pr edit <branch> --repo <repo>`; if that fails, keep the branch (the PR already shows the new commits) and tell the user.
11. Leave the worktree first (`cd` back to the user's checkout if your shell is inside it), then clean up only what you created in step 4: `git worktree remove --force <worktree>`, `git branch -D <branch>`, `git update-ref -d refs/remotes/origin/<branch>`, and any temporary files you created. If you stop before publishing, clean up the same way.
12. Tell the user in one line: the PR link and what it fixes.

The PR states what changed, why, and the evidence: file paths and the commands that failed and then worked. Never include conversation text, the user's words, secrets or details of other repositories.

Never push to the default branch, force-push, merge, or close PRs.

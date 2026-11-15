# Repository instructions

Read [CONTRIBUTING.md](CONTRIBUTING.md) for layout and tests.

- Build only on documented harness extension points: plugins, skills, hooks and settings. No wrappers or launchers.
- The shared hook engine runs on Python 3.9 with the standard library only. OpenCode uses a thin JavaScript plugin adapter with no runtime npm dependencies. Hooks always fail open, never block an action, and stay under 100 ms.
- Hook state holds counters and hashes only; never store prompt text, command arguments or output.
- Keep `skills/boyscout-rule/SKILL.md` portable: frontmatter is `name` and `description` only.
- Tests never install into your personal harness configuration. Use synthetic repositories, the local origin and the fake `gh` in `tests/lib/harness.py`.
- Run the static checks and unit tests on `/usr/bin/python3` and a current Python before reporting verification; run the harness tests when hooks or the skill change.

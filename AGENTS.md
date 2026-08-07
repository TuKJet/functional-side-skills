# Agent repository guidance

Read [`README.md`](README.md) before installing or changing any capability.

- Treat `skills/` entries as globally installable, self-contained Codex Skills.
- Treat `projects/*/.codex/skills/` entries as project-local Skills. Keep their code with the containing project; never install those folders globally unless their project contract is redesigned first.
- Do not create `SKILL.md` for an incubating capability until its trigger, dependencies, input/output contract, and installation shape are decided.
- Never commit business workbooks, SQLite databases, runtime state, backups, generated outputs, virtual environments, caches, or temporary inspection scripts.
- Preserve per-capability `.gitignore` rules when moving or extracting a subproject.
- Run the validation commands documented by the selected capability before committing changes.

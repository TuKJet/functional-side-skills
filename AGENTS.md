# Agent repository guidance

Read [`README.md`](README.md), then enter the selected top-level project and read that project's
`README.md` before installing, using, or changing its Skill.

- Keep `考勤分析/`, `物料编码/`, and `报告自动生成/` as independent project roots.
- Do not reorganize projects by installation type. Global versus project-local delivery is an internal decision of each project.
- Keep implementation, tests, dependencies, installation notes, and Skill metadata inside the owning project.
- Do not create `SKILL.md` for an incubating capability until its trigger, dependencies, input/output contract, and installation shape are decided.
- Never commit business workbooks, SQLite databases, runtime state, backups, generated outputs, virtual environments, caches, or temporary inspection scripts.
- Run the validation commands documented by the selected project before committing changes.

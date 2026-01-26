## ai-team-workflow references (project-owned)

This folder is for **project-specific** reference material that role templates may point to.

Recommended layout:

- `checklists/`
  - `reviewer-checklist-backend.md`
  - `reviewer-checklist-frontend.md`
- `examples/`
  - `technical-design-example.md`

Template placeholder:

- `{{SKILL_DIR}}` resolves to this skill root (the folder containing `SKILL.md`).
  - Example path: `{{SKILL_DIR}}/references/checklists/reviewer-checklist-backend.md`

Notes:

- Keep these files practical. Templates should **reference paths**, not paste long content into prompts.
- You (the operator) can freely edit these files to match your org/project standards.

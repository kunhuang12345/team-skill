---
name: ai-team-workflow
description: Dedicated migration-team workflow built on tmux-workflow/twf (coord -> task_admin -> migrator/reviewer/regress).
---

# ai-team-workflow (migration team)

This branch configures `ai-team-workflow` as a **dedicated automation-test migration team** with a strict per-task chain:

`coord (user-facing root) -> task_admin (one per task) -> migrator/reviewer/regress`

Key properties:
- **One role = one Codex tmux worker** (started via `tmux-workflow` / `twf`).
- All inter-role communication is **inbox-backed** (full bodies written to files under `share/`; CLI receives short notifications).
- Stall detection (`[DRIVE]`) is evaluated **per task chain** (not whole-team).
- Each chain has **exactly 4 nodes**: `task_admin + migrator + reviewer + regress`.

## Dependency

Requires `tmux-workflow` to exist alongside this skill (or set `AITWF_TWF=/path/to/twf`):
- Project install: `./.codex/skills/tmux-workflow/scripts/twf`
- Global install: `~/.codex/skills/tmux-workflow/scripts/twf`

## Shared registry (“task allocation table”)

Default path: `<skill_root>/share/registry.json` (project install example: `./.codex/skills/ai-team-workflow/share/registry.json`).
Overrides (highest → lowest):
- `AITWF_REGISTRY`
- `AITWF_DIR`
- `scripts/atwf_config.yaml` → `share.dir` (legacy: `share_dir`)
- default `<skill_root>/share`

Print the resolved paths:
- `bash .codex/skills/ai-team-workflow/scripts/atwf where`

## Quick start

Initialize + start the initial team + send task to the configured task owner:
- `bash .codex/skills/ai-team-workflow/scripts/atwf init "任务描述：..."` (or `--task-file <path>`)
  - starts root: `coord-main`
  - spawns under root: from `scripts/atwf_config.yaml` → `team.init.children` (default: `task_admin-main`)
  - writes `share/task.md` and sends a task notification to `team.init.task_owner_role` (default: `task_admin`)
  - starts watcher sidecar: `atwf watch-idle` (tmux session `atwf-watch-idle-*`)

Enter a role:
- `bash .codex/skills/ai-team-workflow/scripts/atwf attach coord|task_admin|migrator|reviewer|regress`

Create a full task chain (run inside the `task_admin-*` tmux):
- `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self migrator main --scope "migration execution"`
- `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self reviewer main --scope "code quality gate (changed files only)"`
- `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self regress main --scope "regression test gate (batch)"`

Disband the whole team:
- `bash .codex/skills/ai-team-workflow/scripts/atwf remove <coord-full>`
  - find `<coord-full>` via: `bash .codex/skills/ai-team-workflow/scripts/atwf list`

## Per-role spec docs (config)

Each role can have a required checklist/spec document list injected into its bootstrap message:

```yaml
team:
  role_specs:
    migrator:
      - task/workflow.md
      - task/specs/migration.md
    reviewer:
      - task/specs/review.md
    regress:
      - task/specs/regression.md
```

Paths can be absolute or relative to the project git root.

## Commands

All commands are wrappers around `twf` plus registry/inbox management:
- `bash .codex/skills/ai-team-workflow/scripts/atwf init ["task"] [--task-file PATH] [--registry-only]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf up <role> [label] --scope "..."` (root_role only)
- `bash .codex/skills/ai-team-workflow/scripts/atwf spawn <parent-full> <role> [label] --scope "..."` / `spawn-self ...`
- `bash .codex/skills/ai-team-workflow/scripts/atwf list` / `tree` / `where` / `policy`
- `bash .codex/skills/ai-team-workflow/scripts/atwf inbox` / `inbox-open <id>` / `inbox-ack <id>` / `receipts <msg-id>`
- `bash .codex/skills/ai-team-workflow/scripts/atwf notice ...` / `action ...`
- `bash .codex/skills/ai-team-workflow/scripts/atwf gather ...` / `respond ...` / `reply-needed`
- `bash .codex/skills/ai-team-workflow/scripts/atwf worktree-create[-self]` / `worktree-check-self`
- `bash .codex/skills/ai-team-workflow/scripts/atwf pause` / `unpause` / `stop` / `resume`
- `bash .codex/skills/ai-team-workflow/scripts/atwf watch-idle`
- `bash .codex/skills/ai-team-workflow/scripts/atwf remove <coord-full>`

## Environment knobs

- `AITWF_TWF`: path to `twf` (if not installed next to this skill)
- `AITWF_DIR`: override shared state dir
- `AITWF_REGISTRY`: override registry file path

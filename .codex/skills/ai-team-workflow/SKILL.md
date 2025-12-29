---
name: ai-team-workflow
description: Role-based multi-agent workflow built on tmux-workflow/twf. Use when you want to run a PM/Architect/Product/Dev/QA/Coordinator/Liaison “AI team” as multiple Codex tmux workers, keep a shared responsibilities registry, route internal questions via Coordinator, and escalate user-facing questions via Liaison.
---

# ai-team-workflow

This skill layers a simple “AI team” coordination model on top of the `tmux-workflow` skill (`twf` workers + ask/pend/ping + parent/child spawn).

Core ideas:
- **One role = one Codex worker (tmux session)**.
- **Any role can scale** by spawning a child worker (e.g. `dev` hires `dev-intern`).
- A shared **responsibilities registry** is the source of truth for “who owns what”.
- **Coordinator** routes internal questions; **Liaison** is the only role that asks the user.

## Dependency

Requires `tmux-workflow` to exist alongside this skill (or set `AITWF_TWF=/path/to/twf`):
- Project install: `./.codex/skills/tmux-workflow/scripts/twf`
- Global install: `~/.codex/skills/tmux-workflow/scripts/twf`

## Shared registry (“task allocation table”)

Default path: `<skill_root>/share/registry.json` (project install example: `./.codex/skills/ai-team-workflow/share/registry.json`).
Overrides (highest → lowest):
- `AITWF_REGISTRY`
- `AITWF_DIR`
- `scripts/atwf_config.yaml` → `share_dir`
- default `<skill_root>/share`

Print the resolved paths:
- `bash .codex/skills/ai-team-workflow/scripts/atwf where`

It records, per worker:
- `role`: `pm|arch|prod|dev|qa|coord|liaison`
- `scope`: what this worker owns (used for routing)
- `parent` / `children`: org tree links (mirrors `twf spawn`)

## Quick start

Initialize + start the initial trio + send task to PM:
- `bash .codex/skills/ai-team-workflow/scripts/atwf init "任务描述：/path/to/task.md"`
  - starts: `pm-main`, `coord-main`, `liaison-main`
  - sends the task to PM and prints PM's first reply

If you only want to create the registry (no workers):
- `bash .codex/skills/ai-team-workflow/scripts/atwf init --registry-only`

Create an architect under PM:
- preferred: PM runs inside its tmux: `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self arch user --scope "user module design + task breakdown"`

Create execution roles under an architect:
- preferred: the architect runs inside its tmux:
  - `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self dev backend --scope "backend implementation for user module"`
  - `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self qa user --scope "testing for user module"`
  - `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self prod user --scope "requirements for user module"`

Inspect and route:
- `bash .codex/skills/ai-team-workflow/scripts/atwf list`
- `bash .codex/skills/ai-team-workflow/scripts/atwf route "login" --role dev`

Disband the whole team (requires PM full name):
- `bash .codex/skills/ai-team-workflow/scripts/atwf remove <pm-full>`
  - find `<pm-full>` via: `bash .codex/skills/ai-team-workflow/scripts/atwf list`

## Reporting (mandatory)

Completion/progress must flow upward:
- If you hire subordinates, you are responsible for collecting their reports and then reporting *up* only when the whole subtree is done.
- Default chain: `dev/prod/qa -> arch -> pm -> (coord + liaison) -> user`.

Helpers (run inside tmux worker):
- `bash .codex/skills/ai-team-workflow/scripts/atwf parent-self`
- `bash .codex/skills/ai-team-workflow/scripts/atwf report-up "done summary..."`
- Root PM has no parent in the registry; PM reports to the “collaboration group”:
  - internal: `bash .codex/skills/ai-team-workflow/scripts/atwf report-to coord "status update..."`
  - user-facing: `bash .codex/skills/ai-team-workflow/scripts/atwf report-to liaison "status update for user..."`

## Operating rules (role protocol)

- Team members **do not ask the user directly**.
- When blocked:
  1. Ask **Coordinator**: “Who should I talk to?” / “Is this internal or user-facing?”
  2. Coordinator routes to the best owner using `registry.json` (`atwf route ...`).
  3. Only if unresolved, Coordinator forwards a crisp question to **Liaison**.
  4. Liaison asks the user, then reports back to Coordinator (who distributes).

## Commands

All commands are wrappers around `twf` plus registry management:
- `bash .codex/skills/ai-team-workflow/scripts/atwf init ["task"] [--task-file PATH] [--registry-only]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf up <role> [label] --scope "..."` (start + register + bootstrap)
- `bash .codex/skills/ai-team-workflow/scripts/atwf spawn <parent-full> <role> [label] --scope "..."` (spawn child + register + bootstrap)
- `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self <role> [label] --scope "..."` (inside tmux; uses current worker as parent)
- `bash .codex/skills/ai-team-workflow/scripts/atwf parent <name|full>`
- `bash .codex/skills/ai-team-workflow/scripts/atwf parent-self`
- `bash .codex/skills/ai-team-workflow/scripts/atwf children <name|full>`
- `bash .codex/skills/ai-team-workflow/scripts/atwf children-self`
- `bash .codex/skills/ai-team-workflow/scripts/atwf report-up ["message"]` (inside tmux; stdin supported)
- `bash .codex/skills/ai-team-workflow/scripts/atwf report-to <full|base|role> ["message"]` (inside tmux; stdin supported)
- `bash .codex/skills/ai-team-workflow/scripts/atwf list`
- `bash .codex/skills/ai-team-workflow/scripts/atwf where`
- `bash .codex/skills/ai-team-workflow/scripts/atwf route "<query>" [--role <role>]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf ask <name|full> ["message"]` (stdin supported)
- `bash .codex/skills/ai-team-workflow/scripts/atwf pend <name|full> [N]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf ping <name|full>`
- `bash .codex/skills/ai-team-workflow/scripts/atwf remove <pm-full>` (disband team; clears registry)

## Environment knobs

- `AITWF_TWF`: path to `twf` (if not installed next to this skill)
- `AITWF_DIR`: override shared state dir
- `AITWF_REGISTRY`: override registry file path
- Config file: `.codex/skills/ai-team-workflow/scripts/atwf_config.yaml` (`share_dir`)

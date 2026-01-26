---
name: ai-team-workflow
description: >-
  Role-based multi-agent workflow built on tmux-workflow/twf. Use when you want
  to run a configurable AI team (default: Coord/Admin/Dev/Reviewer/Test) as
  multiple Codex tmux workers, keep a shared responsibilities registry, and
  route work via Coordinator.
---

# ai-team-workflow

This skill layers a simple “AI team” coordination model on top of a tmux-based worker driver (`twf` workers + ask/pend/ping + parent/child spawn).

Core ideas:
- **One role = one Codex worker (tmux session)**.
- **Any role can scale** by spawning a child worker (e.g. `dev` hires `dev-intern`).
- A shared **responsibilities registry** is the source of truth for “who owns what”.
- **Coordinator** routes internal questions and is the only role that asks the user/operator when truly needed.

## Dependency

Self-contained by default:
- Bundles `tmux-workflow` (twf) under `deps/tmux-workflow/`
- Bundles `codex-account-pool` (cap) under `deps/codex-account-pool/` (optional; only used when enabled in config)

Optional overrides:
- `AITWF_TWF=/path/to/twf` to use an external `twf`

## Template portability (important)

This skill supports starting workers in arbitrary working directories (e.g. git worktrees) without requiring `.codex` to exist in those directories.

Rules for humans editing templates/config:
- In `templates/*.md` and `scripts/atwf_config.yaml`, always write runnable commands/paths using placeholders:
  - atwf command: `{{ATWF_CMD}} <subcmd> ...`
  - config path: `{{ATWF_CONFIG}}`
  - skill root (absolute): `{{SKILL_DIR}}` (use for `{{SKILL_DIR}}/references/...`)
- Do **NOT** hardcode `.codex/...` paths or write bare `atwf ...` commands in templates.

Validate (also enforced by `init`/`up`/`spawn` unless `--no-bootstrap`):
- `bash .codex/skills/ai-team-workflow/scripts/atwf templates-check`

Operator note:
- If your current directory does not contain `.codex/`, run `atwf` via its **absolute path** (workers will receive absolute `{{ATWF_CMD}}` commands automatically).

## Shared registry (“task allocation table”)

Default path: `<skill_root>/share/registry.json` (project install example: `./.codex/skills/ai-team-workflow/share/registry.json`).
Overrides (highest → lowest):
- `AITWF_REGISTRY`
- `AITWF_DIR`
- `scripts/atwf_config.yaml` → `share.dir` (legacy: `share_dir`)
- default `<skill_root>/share`

Print the resolved paths:
- `bash .codex/skills/ai-team-workflow/scripts/atwf where`

It records, per worker:
- `role`: one of the project-enabled roles (see `atwf policy` / `scripts/atwf_config.yaml` → `team.policy.enabled_roles`)
- `scope`: what this worker owns (used for routing)
- `parent` / `children`: org tree links (mirrors `twf spawn`)

## Shared artifacts (task + ops)

Within the same share dir as `registry.json`, this skill also standardizes:
- Shared task: `share/task.md` (written by `atwf init ...`)
- Coordinator user-facing log: `share/to_user.md` (append-only; write via `atwf to-user` / `atwf to-user-from-inbox`)
- Ops environment docs:
  - `share/ops/env.md`
  - `share/ops/host-deps.md` (records any host-level installs like `apt`/`curl` downloads)

Note:
- This repo’s default workflow does **NOT** use `share/design/` or `share/design.md` as required collaboration artifacts. Dev design lives in `req_root/technical_design.md` per request.

## Quick start

Initialize + start the initial team (root + configured children):
- `bash .codex/skills/ai-team-workflow/scripts/atwf init "任务描述：/path/to/task.md"`
  - starts root: `<root_role>-<root_label>` (default: `coord-main`)
  - spawns children under root from `scripts/atwf_config.yaml` → `team.init.children` (default in this branch: `[]`, i.e. root-only)
  - copies the task into `share/task.md`
  - sends a `[TASK]` inbox notice to `team.init.task_to_role` (default: `coord`; use `--no-task-notify` or set `task_to_role: ""` to disable)
  - starts a background sidecar: `atwf watch-idle` (tmux session `atwf-watch-idle-*`) to wake `idle` workers when inbox has unread; `atwf pause` disables watcher actions via `share/.paused`
  - note: `atwf pause` stops the watcher session; `atwf unpause` restarts it (so code/config updates take effect)

Enter a role (avoid `tmux a` attaching the wrong session):
- `bash .codex/skills/ai-team-workflow/scripts/atwf attach coord` (or your configured `root_role`)

View org/dependency tree:
- `bash .codex/skills/ai-team-workflow/scripts/atwf tree`

If you only want to create the registry (no workers):
- `bash .codex/skills/ai-team-workflow/scripts/atwf init --registry-only`

If you copied this skill from another repo and `init` reuses stale workers:
- Delete runtime state under this skill’s `share/` (especially `share/registry.json`) and rerun `init`, or
- Run `bash .codex/skills/ai-team-workflow/scripts/atwf init --force-new` to start a fresh initial team.

Default org model in this branch:
- `coord -> admin-REQ-* -> (dev/reviewer/test)`

Create a request subtree (recommended to run inside `coord` tmux):
- `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self admin REQ-001 --scope "delivery owner for REQ-001"`
- Then inside that `admin-REQ-001` tmux, spawn:
  - `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self dev REQ-001 --scope "implementation"`
  - `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self reviewer REQ-001 --scope "code review"`
  - `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self test REQ-001 --scope "verification"`

Inspect and route:
- `bash .codex/skills/ai-team-workflow/scripts/atwf list`
- `bash .codex/skills/ai-team-workflow/scripts/atwf tree coord`
- `bash .codex/skills/ai-team-workflow/scripts/atwf route "login" --role dev`
- `bash .codex/skills/ai-team-workflow/scripts/atwf resolve coord`  # print root full name

Reset/disband (destructive; removes team share dir + worker state):
- `bash .codex/skills/ai-team-workflow/scripts/atwf reset --dry-run`
- `bash .codex/skills/ai-team-workflow/scripts/atwf reset`

## Reporting (mandatory)

Completion/progress must flow upward:
- If you hire subordinates, you are responsible for collecting their reports and then reporting *up* only when the whole subtree is done.
- Default chain (this branch): `dev/reviewer/test -> admin -> coord -> user/operator`.

Helpers (run inside tmux worker):
- `bash .codex/skills/ai-team-workflow/scripts/atwf parent-self`
- `bash .codex/skills/ai-team-workflow/scripts/atwf report-up "done summary..."`
- Subtree owners (`admin-*`) report upward to Coordinator via `report-up`.

## Operating rules (role protocol)

- Team members **do not ask the user directly**.
- When blocked:
  1. Ask **Coordinator**: “Who should I talk to?” / “Is this internal or user-facing?”
  2. Coordinator routes to the best owner using `registry.json` (`atwf route ...`).
  3. If cross-branch communication is needed, Coordinator creates a **handoff** so the two members talk directly (avoid relaying): `atwf handoff ...`
  4. Only if a real **user/operator decision** is required, Coordinator asks the user/operator, then distributes the decision.

User “bounce” rule (assistant is a relay):
- If the user responds with “I don’t understand / shouldn’t this be answerable from docs?”, Coordinator routes it back down to the originator to self-confirm using existing docs (task/docs/assets).
- Only re-escalate to the user when a real **user decision** is required.

## Request → Development workflow (required)

1. Everyone reads the shared task: `share/task.md`.
2. For each request (`REQ-ID`), Admin creates a request workspace directory (`req_root`) and passes `req_id` + `docs_dir` + `req_root` to Dev/Reviewer/Test.
3. Dev creates and maintains: `req_root/technical_design.md` (must match implementation).
4. Each `dev-*` (including interns) creates a dedicated git worktree (no work on current branch):
   - Single-repo (run inside that repo):
     - inside tmux: `bash .codex/skills/ai-team-workflow/scripts/atwf worktree-create-self`
     - then: `cd <git-root>/worktree/<your-full-name>`
   - Multi-module (repo roots are different subdirs; worker started in a non-git dev-workdir):
     - inside tmux: `bash .codex/skills/ai-team-workflow/scripts/atwf worktree-create-self --repo /path/to/module-repo`
     - default location: `<your-work-dir>/<repo-basename>` (override with `--dest-root` + `--name`)
5. Implement + commit + report upward with verification steps. Parent integrates subtree first; Coordinator integrates last.

## Conflict resolution protocol (ordered loop)

For design conflicts or merge conflicts within a subtree:
- Parent selects the conflicting participants (N people) and assigns an order `1..N`.
- Use a strict “token passing” loop: only the current number speaks; after speaking they message the next number.
- When the last (`N`) finishes, loop back to `1`. If `1` declares the conflict resolved, `1` summarizes and reports up; otherwise continue the loop.
- Keep everyone in sync. If broadcast-style messaging is restricted by policy, ask Coordinator to send a `notice` to the right subtree/role (or use direct messages / handoff).

## Commands

All commands are wrappers around `twf` plus registry management:
- `bash .codex/skills/ai-team-workflow/scripts/atwf init ["task"] [--task-file PATH] [--registry-only]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf up <role> [label] --scope "..." [--provider codex|claude] [--work-dir DIR]` (root_role only; start + register + bootstrap)
- `bash .codex/skills/ai-team-workflow/scripts/atwf spawn <parent-full> <role> [label] --scope "..." [--provider codex|claude] [--work-dir DIR]` (spawn child + register + bootstrap)
- `bash .codex/skills/ai-team-workflow/scripts/atwf spawn-self <role> [label] --scope "..." [--provider codex|claude] [--work-dir DIR]` (inside tmux; uses current worker as parent)
- `bash .codex/skills/ai-team-workflow/scripts/atwf parent <name|full>`
- `bash .codex/skills/ai-team-workflow/scripts/atwf parent-self`
- `bash .codex/skills/ai-team-workflow/scripts/atwf children <name|full>`
- `bash .codex/skills/ai-team-workflow/scripts/atwf children-self`
- `bash .codex/skills/ai-team-workflow/scripts/atwf report-up ["message"]` (inside tmux; stdin supported)
- `bash .codex/skills/ai-team-workflow/scripts/atwf report-to <full|base|role> ["message"]` (inside tmux; stdin supported)
- `bash .codex/skills/ai-team-workflow/scripts/atwf list`
- `bash .codex/skills/ai-team-workflow/scripts/atwf where`
- `bash .codex/skills/ai-team-workflow/scripts/atwf to-user --req-id <REQ-ID> --type <...> --need-you "..." --summary "..." [--links "..."]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf to-user-from-inbox <msg-id> --type <...> --need-you "..."`
- `bash .codex/skills/ai-team-workflow/scripts/atwf policy`
- `bash .codex/skills/ai-team-workflow/scripts/atwf perms-self`
- `bash .codex/skills/ai-team-workflow/scripts/atwf tree [root]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf worktree-path <full|base|role>`
- `bash .codex/skills/ai-team-workflow/scripts/atwf worktree-create <full|base|role> [--base REF] [--branch BR] [--repo PATH] [--dest-root DIR] [--name NAME]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf worktree-create-self [--base REF] [--branch BR] [--repo PATH] [--dest-root DIR] [--name NAME]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf worktree-check-self`
- `bash .codex/skills/ai-team-workflow/scripts/atwf stop [--role ROLE|--subtree ROOT|targets...]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf resume [--role ROLE|--subtree ROOT|targets...]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf remove-subtree <root-full|root-base> [--dry-run] [--purge-inbox] [--force]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf pause [--role ROLE|--subtree ROOT|targets...] [--reason "..."]` (human pause; writes `share/.paused` and stops the watcher session)
- `bash .codex/skills/ai-team-workflow/scripts/atwf unpause [--role ROLE|--subtree ROOT|targets...]` (human unpause; clears `share/.paused` and restarts watcher so updates take effect)
- `bash .codex/skills/ai-team-workflow/scripts/atwf notice [--role ROLE|--subtree ROOT|targets...] --message "..."` (or stdin; FYI; no reply expected)
- `bash .codex/skills/ai-team-workflow/scripts/atwf action [--role ROLE|--subtree ROOT|targets...] --message "..."` (or stdin; instruction/task; report deliverables when done)
- `bash .codex/skills/ai-team-workflow/scripts/atwf receipts <msg-id> [--role ROLE|--subtree ROOT|targets...]` (read receipts for notice/any msg-id)
- `bash .codex/skills/ai-team-workflow/scripts/atwf resolve <full|base|role>`
- `bash .codex/skills/ai-team-workflow/scripts/atwf attach <full|base|role>`
- `bash .codex/skills/ai-team-workflow/scripts/atwf route "<query>" [--role <role>]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf ask <full|base|role> ["message"]` (stdin supported)
- Legacy (operator-only; disabled inside worker tmux):
  - `bash .codex/skills/ai-team-workflow/scripts/atwf send <full|base|role> ["message"]`
  - `bash .codex/skills/ai-team-workflow/scripts/atwf broadcast [--role ROLE|--subtree ROOT|targets...] --message "..."` (or stdin)
- `bash .codex/skills/ai-team-workflow/scripts/atwf gather <a> <b> ... --message "..."` (stdin supported; reply-needed fan-in)
- `bash .codex/skills/ai-team-workflow/scripts/atwf respond <req-id> ["message"]` (stdin supported; reply-needed response)
- `bash .codex/skills/ai-team-workflow/scripts/atwf reply-needed [--target <full|base|role>]` (list pending reply-needed)
- `bash .codex/skills/ai-team-workflow/scripts/atwf request <req-id>` (show request status/paths)
- `bash .codex/skills/ai-team-workflow/scripts/atwf handoff <a> <b> [--reason "..."] [--ttl SECONDS]`

Legacy (optional; not used by default in this repo):
- `bash .codex/skills/ai-team-workflow/scripts/atwf design-path <full|base|role>`
- `bash .codex/skills/ai-team-workflow/scripts/atwf design-init <full|base|role> [--force]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf design-init-self [--force]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf pend <full|base|role> [N]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf ping <full|base|role>`
- `bash .codex/skills/ai-team-workflow/scripts/atwf drive [running|standby]` (drive mode lives in config; watcher hot-reloads; setting mode must be outside worker tmux)
- `bash .codex/skills/ai-team-workflow/scripts/atwf state [target]`
- `bash .codex/skills/ai-team-workflow/scripts/atwf state-self`
- `bash .codex/skills/ai-team-workflow/scripts/atwf state-set-self <working|draining|idle>` (debug only; watcher overwrites)
- `bash .codex/skills/ai-team-workflow/scripts/atwf watch-idle [--interval S] [--delay S] [--once]`

## Environment knobs

- `AITWF_TWF`: path to external `twf` (override bundled `deps/tmux-workflow`)
- `AITWF_DIR`: override shared state dir
- `AITWF_REGISTRY`: override registry file path
- `AITWF_PROJECT_ROOT`: stable project root for watcher/session naming (optional; otherwise derived from install location, falling back to cwd/git root)
- Config file: `.codex/skills/ai-team-workflow/scripts/atwf_config.yaml` (unified: `codex.*`, `twf.*`, `cap.*`, `share.*`, `team.*`)
- `codex.python_venv`: optional Python virtualenv for Codex workers (exports `VIRTUAL_ENV` and prepends `<venv>/bin` to `PATH`; e.g. `/root/.virtualenvs/uxbot`)

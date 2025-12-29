# tmux-workflow: design notes & changes

This document records the key design decisions for the `tmux-workflow` skill (and the ergonomics you asked for).

## Goals

- A **portable**, self-contained skill directory: copy `tmux-workflow/` anywhere and it works.
- No `ccb` naming in this skill; everything is `twf` / `TWF_*`.
- Short, safe commands:
  - Start with a base name, auto-generate a unique full name with timestamp
  - “Use latest” behavior for `<base>` to avoid ambiguity
  - Destructive actions require full-name
- Parent/child worker tree:
  - `stop/resume` for non-destructive lifecycle
  - `spawn` to create child nodes
  - `tree/list` for visibility
  - `remove` defaults to recursive cleanup
- Robust tmux submit to avoid “typed but not sent”.

## Naming & identity

- **Base**: `codex-a`
- **Full name / worker id**: `codex-a-YYYYmmdd-HHMMSS-<pid>`
- Full name is used as:
  - tmux session name
  - `worker_id`
  - default per-worker `CODEX_HOME` folder name

Rationale: “one stable string” avoids cross-talk and makes it easy for a worker to know “who am I”:

- Inside tmux: `bash .codex/skills/tmux-workflow/scripts/twf self`

## State location (global, not cwd)

- Default `TWF_STATE_DIR` is **global**: `~/.twf/`
  - Reason: workers may `cd` / use worktrees; state must remain discoverable from any directory.
- Each worker has one state file: `~/.twf/<full>.json`

The state file is the primary index for:

- tmux target (`tmux_session`, `tmux_target`, `pane_id`)
- where to find logs (`codex_home`, `codex_session_root`, `codex_session_path`)
- parent/child relationship (`parent`, `children[]`)
- resume tracking (`codex_resume_from_id`, `codex_current_id`)

## Worker isolation (`CODEX_HOME`)

- Default per-worker home: `~/.codex-workers/<worker_id>`
- On start/resume, we sync from `~/.codex` into the worker home (excluding `sessions/`, `log/`, `history.jsonl`) so each worker has:
  - its own `sessions/` logs
  - its own settings/skills snapshot

## Parent/child semantics and cycles

- `twf spawn <parent-full> <child-base>` **creates a new child worker** and records:
  - child: `parent=<parent-full>`
  - parent: `children[] += <child-full>`
- It does **not** “bind two existing arbitrary workers”.

Cycles should not occur via `spawn` (child is new). If state is edited manually, `twf tree` and `twf resume` are cycle-safe (visited set).

## Submit reliability (“typed but not sent”)

Observed behavior: some Codex TUI states treat fast terminal injection as “paste”, so Enter may not submit immediately.

Mitigations:

- Start Codex with `-c disable_paste_burst=true --sandbox danger-full-access` (default).
- Inject using tmux buffer-paste for multiline/long prompts.
- Add an explicit delay before submit:
  - `TWF_SUBMIT_DELAY` (default `0.5` seconds)
- If logs do not show a new user message after injection, send extra Enter keypresses:
  - `TWF_SUBMIT_NUDGE_AFTER` (default `0.7` seconds)
  - `TWF_SUBMIT_NUDGE_MAX` (default `2`)

“Send success” is determined by Codex JSONL logs (a new user message entry after the injection baseline).

## Destructive cleanup safety

- `twf remove` requires a **full name**.
- It only deletes a worker’s `codex_home` if it is under `TWF_WORKERS_DIR` (or default `~/.codex-workers`) to reduce the risk of deleting arbitrary paths.
- State operations are locked with `flock` on `~/.twf/.lock`.

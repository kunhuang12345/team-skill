# tmux-workflow: design notes (twf)

This file records the key ergonomics and behavior implemented in `scripts/`.

## Goals

- Portable, self-contained skill directory: copy `tmux-workflow/` and it works.
- Short commands with safe defaults:
  - Base name → auto full name with timestamp
  - `<base>` resolves to “latest full name”
  - Destructive operations require full name
- Multi-worker tree:
  - `spawn` creates a new child worker and records parent/children in state
  - `stop/resume` for lifecycle
  - `tree/list` for visibility
  - `remove` defaults to recursive cleanup
- Reliable submit to avoid “typed but not sent”.

## Naming & identity

- Base: `codex-a`
- Full name / worker id: `codex-a-YYYYmmdd-HHMMSS-<pid>` (also tmux session name)
- Inside tmux, a worker can identify itself via:
  - `bash .codex/skills/tmux-workflow/scripts/twf self`

## State location (configurable)

Configured in `scripts/twf_config.yaml`:

- `twf_state_dir_mode: auto` → `<skill_root>/.twf/` (default)
- `twf_state_dir_mode: global` → `~/.twf/` (best for worktrees)
- `twf_state_dir_mode: manual` → `twf_state_dir` (must be set; relative to CWD)

`TWF_STATE_DIR=/some/path` overrides all.

## Worker isolation (`CODEX_HOME`)

- Default per-worker home: `~/.codex-workers/<worker_id>`
- On start/resume we sync from `~/.codex` into the worker home (excluding `sessions/`, `log/`, `history.jsonl`).

## Submit reliability

Mitigations in `scripts/codex_ask.py`:

- tmux buffer paste for multiline/long prompts
- delay before Enter: `TWF_SUBMIT_DELAY` (default `0.5`)
- if logs don’t acknowledge a new user message, nudge Enter:
  - `TWF_SUBMIT_NUDGE_AFTER` (default `0.7`)
  - `TWF_SUBMIT_NUDGE_MAX` (default `2`)

“Send success” is judged by Codex JSONL logs (a new user message entry after the injection baseline).

## Log waiting mode (Linux/WSL)

Default `TWF_WATCH_MODE=auto`:

- Linux/WSL: use `inotify` to block until the JSONL file changes (low CPU)
- fallback: polling with `TWF_POLL_INTERVAL` (default `0.05s`)

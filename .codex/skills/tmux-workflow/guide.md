# tmux-workflow guide (twf)

`twf` = **t**mu**x**-**w**ork**f**low (this skill’s recommended wrapper: `scripts/twf`).

## What it does

- Starts/reuses Codex CLI “workers” in tmux sessions.
- Injects prompts into a worker pane and **waits for the next reply** by polling the worker’s Codex `sessions/**/*.jsonl` logs.
- Runs each worker with an isolated `CODEX_HOME` (default: `~/.codex-workers/<worker_id>`) to avoid log cross-talk.

## State & where things live

- **TWF state (global):** `~/.twf/` (override with `TWF_STATE_DIR`)
  - One JSON per worker: `~/.twf/<worker_full_name>.json`
- **Worker homes (global):** `~/.codex-workers/<worker_id>` (override with `TWF_WORKERS_DIR`)
  - Logs: `~/.codex-workers/<worker_id>/sessions/**/*.jsonl`
- **tmux sessions:** worker full name == tmux session name

## Naming rules (base vs full name)

- **Base name:** short label like `codex-a`
- **Full name:** auto unique id: `<base>-YYYYmmdd-HHMMSS-<pid>`
- `twf <base>` creates a new worker with a new full name.
- `twf <base> "msg"` auto-selects the **latest** worker for that base; if none exists, it auto-creates one.
- `twf remove` requires a **full name** (safety).

## Common commands

Start / ask:

- Start a new worker: `bash .codex/skills/tmux-workflow/scripts/twf codex-a`
- Ask the latest worker: `bash .codex/skills/tmux-workflow/scripts/twf codex-a "你好"`

Inspect:

- Latest reply / last N rounds: `bash .codex/skills/tmux-workflow/scripts/twf pend codex-a [N]`
- Health check: `bash .codex/skills/tmux-workflow/scripts/twf ping codex-a`
- Flat list: `bash .codex/skills/tmux-workflow/scripts/twf list`
- Tree view: `bash .codex/skills/tmux-workflow/scripts/twf tree`

Stop / resume:

- Stop tmux session (keep state + logs): `bash .codex/skills/tmux-workflow/scripts/twf stop codex-a`
- Resume (default resumes subtree): `bash .codex/skills/tmux-workflow/scripts/twf resume codex-a`
- Resume only this node: `bash .codex/skills/tmux-workflow/scripts/twf resume codex-a --no-tree`

Parent/child (“sub-codex”):

- Spawn a child and link it: `bash .codex/skills/tmux-workflow/scripts/twf spawn <parent-full> codex-sub`
- Inside a worker tmux pane, spawn from “self”:
  - `bash .codex/skills/tmux-workflow/scripts/twf self`
  - `bash .codex/skills/tmux-workflow/scripts/twf spawn-self codex-sub`

Remove (destructive):

- Recursive delete subtree (default): `bash .codex/skills/tmux-workflow/scripts/twf remove <full-name>`
- Delete only one node: `bash .codex/skills/tmux-workflow/scripts/twf remove <full-name> --no-recursive`

## tmux quick references

- List sessions: `tmux ls` (or `tmux list-sessions`)
- Attach: `tmux attach -t <session>`
- Kill: `tmux kill-session -t <session>`
- List panes (all): `tmux list-panes -a -F '#S:#I.#P #{pane_id} #{pane_current_command} #{pane_current_path}'`

## Why “typed but not sent” can happen

Some Codex TUI states (startup / paste-burst handling) may interpret fast terminal injection as a “paste”, so an Enter keypress may become a line-break or get ignored until the UI is ready.

Mitigations used by this skill:

- Start Codex with `-c disable_paste_burst=true` (default `TWF_CODEX_CMD`).
- Delay submit after injection: `TWF_SUBMIT_DELAY` (default `0.5` seconds).
- Log-ack based retries: `TWF_SUBMIT_NUDGE_AFTER` / `TWF_SUBMIT_NUDGE_MAX` send extra Enter keypresses until Codex logs show the user message was accepted.

## How “send success” is detected

`scripts/codex_ask.py` watches the worker’s `sessions/**/*.jsonl` and treats a prompt as “submitted” when a new user-message entry appears (for example `type=event_msg` with `payload.type=user_message`) after the injection baseline.


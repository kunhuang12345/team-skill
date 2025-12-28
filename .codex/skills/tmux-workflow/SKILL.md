---
name: tmux-workflow
description: Portable tmux-based workflow to drive one or multiple long-running Codex CLI “workers” from another process (e.g. Claude CLI) by (1) starting/reusing a tmux session running `codex`, (2) injecting prompts into the Codex pane via `tmux send-keys`/buffer paste, and (3) polling the worker’s Codex JSONL session logs to extract the next assistant reply. Each worker runs with an isolated `CODEX_HOME` (default `~/.codex-workers/<worker_id>`) to prevent log cross-talk when multiple Codex workers run concurrently. Use when you need to submit/wait/read Codex replies in tmux, manage multiple Codex workers, or troubleshoot the tmux worker and log binding.
---

# tmux-workflow

This skill is a self-contained toolkit under `scripts/` (copy this whole folder to any repo’s `.codex/skills/` or to `~/.codex/skills/`).

## Quick start (Claude → Codex)

1. (Optional) Check deps:
   - `bash .codex/skills/tmux-workflow/scripts/check_deps.sh`
2. Start a worker and ask with short names (state under `./.twf/`):
   - `bash .codex/skills/tmux-workflow/scripts/twf codex-a`
   - `bash .codex/skills/tmux-workflow/scripts/twf codex-a "你好"`

## Scripts

- `scripts/codex_up_tmux.sh`: start/reuse the worker and write `./.codex-tmux-session.json` (JSON) in the current directory; sync `~/.codex` into per-worker `CODEX_HOME`.
- `scripts/codex_ask.py`: inject text into the tmux pane and poll Codex `sessions/*.jsonl` until the next assistant reply appears.
- `scripts/codex_pend.py`: print the latest reply (or last N Q/A pairs) from the bound or auto-detected Codex log.
- `scripts/codex_ping.py`: health check for tmux worker and log binding.
- `scripts/twf`: helper wrapper for `up/ask/pend/ping/remove` with automatic session-file naming and “pick latest” behavior.

## Environment knobs

- `TWF_SESSION_FILE`: session file path (default: `./.codex-tmux-session.json`).
- `TWF_TMUX_SESSION`: override tmux session name (default: `codex-<hash(cwd)>`).
- `TWF_CODEX_CMD`: command used inside tmux (default: `codex -c disable_paste_burst=true`).
- `TWF_WORKERS_DIR`: per-worker `CODEX_HOME` base dir (default: `~/.codex-workers`).
- `TWF_CODEX_HOME_SRC`: source `CODEX_HOME` to copy from (default: `~/.codex`).
- `TWF_CODEX_SESSION_ROOT` / `CODEX_SESSION_ROOT` / `CODEX_HOME`: where to scan logs (default: `~/.codex/sessions`).
- `TWF_POLL_INTERVAL` (seconds, default `0.05`), `TWF_TIMEOUT` (seconds, default `3600`).
- `TWF_STATE_DIR`: twf wrapper state dir (default: `./.twf`).

See `guide.md` for the design, acceptance checklist, and notes.

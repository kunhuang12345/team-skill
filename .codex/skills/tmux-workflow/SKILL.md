---
name: tmux-workflow
description: Portable tmux-based workflow to drive a long-running Codex CLI “worker” from another process (e.g. Claude CLI) by (1) starting/reusing a tmux session running `codex`, (2) injecting prompts into the Codex pane via `tmux send-keys`/buffer paste, and (3) polling Codex JSONL session logs under `~/.codex/sessions/**/*.jsonl` to extract the next assistant reply. Use when you need to submit/wait/read Codex replies in tmux, troubleshoot the tmux worker, or fetch recent Codex replies from logs.
---

# ccb-tmux-workflow

This skill is a self-contained toolkit under `scripts/` (copy this whole folder to any repo’s `.codex/skills/` or to `~/.codex/skills/`).

## Quick start (Claude → Codex)

1. Start/reuse a tmux worker running `codex`:
   - `bash .codex/skills/ccb-tmux-workflow/scripts/check_deps.sh`
   - `bash .codex/skills/ccb-tmux-workflow/scripts/codex_up_tmux.sh`
2. Send a prompt and wait for the next Codex reply (stdout = reply only):
   - `python3 .codex/skills/ccb-tmux-workflow/scripts/codex_ask.py "你好"`

## Scripts

- `scripts/codex_up_tmux.sh`: start/reuse the worker and write `.codex-session` (JSON) in the current directory.
- `scripts/codex_ask.py`: inject text into the tmux pane and poll Codex `sessions/*.jsonl` until the next assistant reply appears.
- `scripts/codex_pend.py`: print the latest reply (or last N Q/A pairs) from the bound or auto-detected Codex log.
- `scripts/codex_ping.py`: health check for tmux worker and log binding.

## Environment knobs

- `CCB_CODEX_SESSION_FILE`: session file path (default: `./.ccb-codex-session.json`).
- `CCB_TMUX_SESSION`: override tmux session name (default: `codex-<hash(cwd)>`).
- `CCB_CODEX_CMD`: command used inside tmux (default: `codex -c disable_paste_burst=true`).
- `CCB_CODEX_SESSION_ROOT` / `CODEX_SESSION_ROOT` / `CODEX_HOME`: where to scan logs (default: `~/.codex/sessions`).
- `CCB_POLL_INTERVAL` (seconds, default `0.05`), `CCB_TIMEOUT` (seconds, default `3600`).

See `guide.md` for the design, acceptance checklist, and notes.

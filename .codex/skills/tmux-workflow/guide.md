# tmux-workflow guide (twf)

`twf` is the recommended wrapper script for this skill (`tmux-workflow`).

## Quick start

```bash
# deps: tmux + python3 + codex
bash .codex/skills/tmux-workflow/scripts/check_deps.sh

# start a worker (base name -> full name with timestamp)
bash .codex/skills/tmux-workflow/scripts/twf codex-a

# ask (uses latest worker under the same base name)
bash .codex/skills/tmux-workflow/scripts/twf codex-a "你好"
```

## Naming (base vs full name)

- Base name: `codex-a`
- Full name (auto): `codex-a-YYYYmmdd-HHMMSS-<pid>` (also the tmux session name)
- `twf <base> "msg"` picks the latest full-name worker for that base (by state-file mtime).
- Destructive `remove` requires a **full name**.

## Parent/child (“sub-codex”)

Create a child worker and record the relationship in state:

```bash
# parent must be full name
bash .codex/skills/tmux-workflow/scripts/twf spawn <parent-full> codex-sub

# inside tmux: parent is “self”
bash .codex/skills/tmux-workflow/scripts/twf spawn-self codex-sub
```

## Inspect / lifecycle

```bash
bash .codex/skills/tmux-workflow/scripts/twf list
bash .codex/skills/tmux-workflow/scripts/twf tree

bash .codex/skills/tmux-workflow/scripts/twf stop codex-a
bash .codex/skills/tmux-workflow/scripts/twf resume codex-a --no-tree
```

## Cleanup (destructive)

```bash
# must be full name; default recursive (subtree)
bash .codex/skills/tmux-workflow/scripts/twf remove <full-name>
```

## Where state/logs live

- twf state JSON files: controlled by `scripts/twf_config.yaml` (`twf_state_dir_mode`) or `TWF_STATE_DIR`.
- Per-worker `CODEX_HOME` (isolated): `~/.codex-workers/<worker_id>/`
- Codex session logs: `<CODEX_HOME>/sessions/**/*.jsonl`

More details: see `.codex/skills/tmux-workflow/SKILL.md`.

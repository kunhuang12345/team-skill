# Team Command Governance (Mandatory)

This rule applies to **every role** in this AI team.

## Allowed messaging path (required)

- **Send messages only via** `atwf` / `twf` wrappers:
  - Broadcast: `bash .codex/skills/ai-team-workflow/scripts/atwf broadcast ...`
  - Direct message: `bash .codex/skills/ai-team-workflow/scripts/atwf ask ...` or `bash .codex/skills/tmux-workflow/scripts/twf ask ...`
  - Fire-and-forget (no waiting): `bash .codex/skills/tmux-workflow/scripts/twf send ...`

## Forbidden (do NOT do this)

Do **NOT** use raw tmux keystroke injection to “send” chat messages, including:

- `tmux set-buffer ...`
- `tmux paste-buffer ...`
- `tmux send-keys ...`
- any custom shell loop that pastes into sessions/panes and presses Enter

Reason: it is unreliable (can target the wrong pane, Enter can become newline, paste can be dropped), and it breaks team consistency/debuggability.

## If you think an exception is needed

- Ask **Coordinator** first and wait for approval.
- If approved, document the exact command and outcome in `{{TEAM_DIR}}/design/<your-full>.md` so the team can reproduce/debug.


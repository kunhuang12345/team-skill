# Team Command Governance (Mandatory)

This rule applies to **every role** in this AI team.

## Allowed messaging path (required)

- **Send messages only via** `atwf` / `twf` wrappers:
  - Broadcast: `bash .codex/skills/ai-team-workflow/scripts/atwf broadcast ...`
  - Direct message: `bash .codex/skills/ai-team-workflow/scripts/atwf ask ...` or `bash .codex/skills/tmux-workflow/scripts/twf ask ...`
  - Fire-and-forget (no waiting): `bash .codex/skills/tmux-workflow/scripts/twf send ...`
- Any progress/completion/design conclusion **must** be reported via `atwf report-up` (or `atwf report-to coord|liaison`) to count as “reported”; otherwise the parent may treat it as “not received”.

## Forbidden (do NOT do this)

Do **NOT** use raw tmux keystroke injection to “send” chat messages, including:

- `tmux set-buffer ...`
- `tmux paste-buffer ...`
- `tmux send-keys ...`
- any custom shell loop that pastes into sessions/panes and presses Enter

Reason: it is unreliable (can target the wrong pane, Enter can become newline, paste can be dropped), and it breaks team consistency/debuggability.

## Messaging etiquette (required)

- Recipients may be busy; replies can be delayed.
- Do not spam: if you need a reply, wait; do not re-send the same message repeatedly.
- Do not stall: unless you *must* have the recipient’s input to proceed, keep working on parallel tasks in your own scope (design/docs/tests/verification/notes) while you wait.
- If you suspect delivery failed, ask **Coordinator** to verify via logs; do not invent new raw tmux paste/send scripts.

## If you think an exception is needed

- Ask **Coordinator** first and wait for approval.
- If approved, document the exact command and outcome in `{{TEAM_DIR}}/design/<your-full>.md` so the team can reproduce/debug.

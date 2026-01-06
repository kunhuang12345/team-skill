# Team Command Governance (Mandatory)

This rule applies to **every role** in this AI team.

## Allowed messaging path (required)

- **Send messages only via** `atwf` wrappers (policy-enforced):
  - Broadcast (usually only `coord`): `bash .codex/skills/ai-team-workflow/scripts/atwf broadcast ...`
  - Direct message (wait for reply): `bash .codex/skills/ai-team-workflow/scripts/atwf ask ...`
  - Direct message (no waiting): `bash .codex/skills/ai-team-workflow/scripts/atwf send ...`
  - Handoff/authorization (avoid relaying): `bash .codex/skills/ai-team-workflow/scripts/atwf handoff ...`
- Check hard permissions any time:
  - `bash .codex/skills/ai-team-workflow/scripts/atwf policy`
  - `bash .codex/skills/ai-team-workflow/scripts/atwf perms-self`
- Any progress/completion/design conclusion **must** be reported via `atwf report-up` (or `atwf report-to coord|liaison`) to count as “reported”; otherwise the parent may treat it as “not received”.

## Queue-safe message protocol (mandatory)

Codex TUI can collapse multiple pending messages into a single prompt (especially after `Esc`).
To keep ordering deterministic, **all atwf messages are wrapped** like:

```
[ATWF-MSG id=000123 kind=send from=<full> to=<full> ts=<iso8601>]
...body...
[ATWF-END id=000123]
```

Hard rules:
- Extract all `[ATWF-MSG ...] ... [ATWF-END ...]` blocks from the prompt.
- Process blocks in **ascending numeric `id`** (oldest first), regardless of where they appear in the prompt.
- If the same `id` appears multiple times, **process it once** (dedupe).
- Reply **once per batch**. Start your reply with: `ACK ids: 000123, 000124`.
- Do not re-quote entire incoming messages; reference by `id` to save tokens.
- For `kind=send|broadcast|bootstrap|handoff|task`: keep replies minimal (ACK + any required action only).

## Inbox-backed message bodies (mandatory)

To reduce token waste and avoid “confirm storms”, **the full message body is written to a file** and the chat message only contains a short `[INBOX] id=...` notification.

Hard rules:
- For every received `id`, load the full body via: `bash .codex/skills/ai-team-workflow/scripts/atwf inbox-open <id>`
- After you have fully processed the message, mark it read via: `bash .codex/skills/ai-team-workflow/scripts/atwf inbox-ack <id>`
- To see your pending unread queue: `bash .codex/skills/ai-team-workflow/scripts/atwf inbox`
- When you send many messages to the same target, check backlog first: `bash .codex/skills/ai-team-workflow/scripts/atwf inbox-pending <target>`

## CLI injection policy (mandatory)

Goal: avoid spending tokens by injecting prompts into other workers' Codex CLIs.

Hard rules:
- Default delivery is **inbox-only**: `atwf send` / `atwf broadcast` / `atwf report-*` write to inbox and do **not** inject into the recipient CLI.
- The **only** routine CLI injection is the operator-side watcher `atwf watch-idle`, which wakes `idle` workers when inbox has pending items.
- `--notify` / `--wait` are **operator-only exceptions** and should not be used during normal work.

## Agent state + standby protocol (mandatory)

The team uses an explicit state machine to reduce token waste and prevent message pileups.

States:
- `working`: you are actively executing an assigned task; you must proactively check inbox periodically.
- `draining`: you are finishing work and clearing inbox before going idle.
- `idle`: you are on standby; you must NOT proactively poll inbox (a watcher will wake you).

Commands:
- View your state: `bash .codex/skills/ai-team-workflow/scripts/atwf state-self`
- Set your state: `bash .codex/skills/ai-team-workflow/scripts/atwf state-set-self <working|draining|idle>`
- View others: `bash .codex/skills/ai-team-workflow/scripts/atwf state <target>`

Hard rules:
- When `working`: run `bash .codex/skills/ai-team-workflow/scripts/atwf inbox` at least once per minute (or before any blocking wait). If it lists unread `id`s, you must `inbox-open` + process + `inbox-ack` them, then continue work.
- When your assigned work is complete: do NOT jump directly to `idle`.
  1) set `draining`
  2) clear inbox to empty (process+ack all unread)
  3) only then set `idle`
- Before setting `idle`, you must confirm inbox is empty; if new messages arrive after you go `idle`, that is OK: a watcher will wake you with a short prompt.
- When `idle`: do not poll inbox on your own; wait for a wake prompt, then set `working` and process inbox.

## Forbidden (do NOT do this)

Do **NOT** use raw tmux keystroke injection to “send” chat messages, including:

- `tmux set-buffer ...`
- `tmux paste-buffer ...`
- `tmux send-keys ...`
- any custom shell loop that pastes into sessions/panes and presses Enter

Reason: it is unreliable (can target the wrong pane, Enter can become newline, paste can be dropped), and it breaks team consistency/debuggability.

Also do **NOT** bypass policy by using `twf ask/send` directly for inter-role messaging.
If you must debug a delivery issue, ask **Coordinator** for approval and document the exact commands.

## Messaging etiquette (required)

- Recipients may be busy; replies can be delayed.
- Do not spam: if you need a reply, wait; do not re-send the same message repeatedly.
- Do not stall: unless you *must* have the recipient’s input to proceed, keep working on parallel tasks in your own scope (design/docs/tests/verification/notes) while you wait.
- If you suspect delivery failed, ask **Coordinator** to verify via logs; do not invent new raw tmux paste/send scripts.

## If you think an exception is needed

- Ask **Coordinator** first and wait for approval.
- If approved, document the exact command and outcome in `{{TEAM_DIR}}/design/<your-full>.md` so the team can reproduce/debug.

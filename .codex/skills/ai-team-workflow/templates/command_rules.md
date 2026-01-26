# Team Command Governance (Mandatory)

This rule applies to **every role** in this AI team.

Template authoring rule (for humans editing templates):
- Always write runnable commands using placeholders:
  - atwf command: `{{ATWF_CMD}} <subcmd> ...`
  - config path: `{{ATWF_CONFIG}}`
- Do **NOT** hardcode `.codex/...` paths or write bare `atwf` commands in templates.

## Allowed messaging path (required)

- **Send messages only via** `atwf` wrappers (policy-enforced):
  - Notice (FYI, no reply expected): `{{ATWF_CMD}} notice ...`
  - Action (instruction, no immediate ACK): `{{ATWF_CMD}} action ... --file <path>`
  - Reply-needed (must reply): `{{ATWF_CMD}} gather ...` / `{{ATWF_CMD}} respond ...`
  - Direct question (discouraged; may require CLI injection): `{{ATWF_CMD}} ask ...`
  - Handoff/authorization (avoid relaying): `{{ATWF_CMD}} handoff ...`
  - Legacy (operator-only): `{{ATWF_CMD}} send` / `{{ATWF_CMD}} broadcast` (disabled inside worker tmux; use `notice` / `action`)
- Check hard permissions any time:
  - `{{ATWF_CMD}} policy`
  - `{{ATWF_CMD}} perms-self`
- Any progress/completion **must** be reported via `{{ATWF_CMD}} report-up` (or `{{ATWF_CMD}} report-to coord`) to count as “reported”; otherwise the parent may treat it as “not received”.

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
- For every received `id`, load the full body via: `{{ATWF_CMD}} inbox-open <id>`
- After you have fully processed the message, mark it read via: `{{ATWF_CMD}} inbox-ack <id>`
- To see your pending unread queue: `{{ATWF_CMD}} inbox`
- When you send many messages to the same target, check backlog first: `{{ATWF_CMD}} inbox-pending <target>`

## Message intents (hard protocol) (mandatory)

All cross-role messages MUST be one of:

1) `notice` (notification / FYI)
- Sender uses: `{{ATWF_CMD}} notice ...`
- Receiver MUST: `{{ATWF_CMD}} inbox-open <id>` → read → `{{ATWF_CMD}} inbox-ack <id>`.
- Receiver MUST NOT: reply upward with “received/ok/ack” via `report-up`/`action`/`ask`.

2) `reply-needed` (explicit answer required)
- Sender uses: `{{ATWF_CMD}} gather ...`
- Receiver MUST use: `{{ATWF_CMD}} respond ...` (or `--blocked --snooze --waiting-on ...`).

3) `action` (instruction / task)
- Sender uses: `{{ATWF_CMD}} action ... --file <path>`
- Always write the message body to a real file first (recommended location: `{{TEAM_DIR}}/tmp/action-*.md`).
- Receiver MUST NOT: send an immediate ACK message.
- Receiver MUST: execute; when done, report deliverables/evidence via `report-up` (or `report-to`).

## Notice receipts (replace ACK storms) (mandatory)

To confirm “everyone read the notice”, do NOT ask for ACK replies.
Instead, query receipts by `msg_id`:

- `{{ATWF_CMD}} receipts <msg_id>`

Statuses:
- `unread` / `overflow` / `read` / `missing`

## Reply-needed requests (gather/respond) (mandatory)

Use this to collect multiple replies without spamming chat threads.

Hard rules:
- If you need multiple people to reply, use `{{ATWF_CMD}} gather` (not N separate `ask` + repeated summaries).
  - Initiator: `{{ATWF_CMD}} gather <a> <b> ... --topic "..." --message "<request>"` (or via stdin)
- If you receive a `kind=reply-needed` inbox message, do **NOT** reply via `send/ask`.
  Use `{{ATWF_CMD}} respond` so the system can consolidate:
  - Reply: `{{ATWF_CMD}} respond <req-id> "<your reply>"`
  - Blocked/snooze: `{{ATWF_CMD}} respond <req-id> --blocked --snooze 15m --waiting-on <base> "why blocked"`
  - List your pending reply-needed: `{{ATWF_CMD}} reply-needed`
- The initiator receives **one** consolidated result message (inbox-only) when all targets replied or the request times out.
- If you see a `REPLY wake:` prompt, run `{{ATWF_CMD}} reply-needed` immediately and handle the oldest due request.

## CLI injection policy (mandatory)

Goal: avoid spending tokens by injecting prompts into other workers' Codex CLIs.

Hard rules:
- Default delivery is **inbox-only**: `{{ATWF_CMD}} notice` / `{{ATWF_CMD}} action` / `{{ATWF_CMD}} report-*` write to inbox and do **not** inject into the recipient CLI.
- The **only** routine CLI injection is the operator-side watcher `{{ATWF_CMD}} watch-idle`, which:
  - wakes `idle` workers when inbox has pending items
  - injects governance alerts to `coord` when `working` workers ignore inbox too long
  - may auto-send an `Enter` keystroke when an approval menu is detected (config: `team.state.auto_enter`)
- `--notify` / `--wait` are **operator-only exceptions** and should not be used during normal work.

## Agent state protocol (mandatory)

State is **watcher-derived** (operator sidecar `{{ATWF_CMD}} watch-idle`) to reduce token waste and avoid relying on workers to self-report.

Derived states (informational; enforced by watcher):
- `working`: watcher sees recent tmux output activity, or the worker is within a short grace period after a wake prompt.
- `idle`: watcher sees no recent output activity.

Commands:
- View your derived state: `{{ATWF_CMD}} state-self`
- View others: `{{ATWF_CMD}} state <target>`

Hard rules:
- If you are actively working (or after any wake prompt): run `{{ATWF_CMD}} inbox` at least once per minute (or before any blocking wait). If it lists unread `id`s, you must `inbox-open` + process + `inbox-ack` them, then continue work.
- Do **NOT** use `state-set-self` as part of normal workflow; it will be overwritten by the watcher and is only for debugging.
- When you have nothing actionable: simply wait. If new inbox arrives, the watcher will wake you with a short prompt.

## Drive loop (mandatory)

To prevent “everyone idle, nobody kicks off the next iteration”, the team uses a human-controlled drive loop.

Hard rules:
- `team.drive.mode` is USER/OPERATOR-ONLY configuration.
- Any worker (including `coord`) MUST NOT edit: `{{ATWF_CONFIG}}`.
- `[DRIVE]` means **“ALL IDLE + INBOX EMPTY = abnormal stall”** (no one is driving work).
- On `[DRIVE]`, the driver’s only job is:
  - diagnose the root cause (run: `{{ATWF_CMD}} state`, `{{ATWF_CMD}} list`, `{{ATWF_CMD}} inbox`), and
  - re-drive work by assigning `action` tasks (owners + next action + ETA) or presenting concrete blocker evidence (with handoff when needed).

## Git integration boundary (mandatory)

- Workers MAY create local branches/worktrees and make local commits.
- Workers MUST NOT run operator-only integration commands: `git merge`, `git rebase`, `git pull`, `git push`.
- Handoff MUST include: branch name + commit SHA(s) + exact verify commands/results, so the operator can re-review and integrate.

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
- Do not stall: unless you *must* have the recipient’s input to proceed, keep working on parallel tasks in your own scope (docs/tests/verification/notes) while you wait.
- If you suspect delivery failed, ask **Coordinator** to verify via logs; do not invent new raw tmux paste/send scripts.

## If you think an exception is needed

- Ask **Coordinator** first and wait for approval.
- If approved, document the exact command and outcome in `{{TEAM_DIR}}/ops/env.md` (or if host installs were involved: `{{TEAM_DIR}}/ops/host-deps.md`) so the team can reproduce/debug.

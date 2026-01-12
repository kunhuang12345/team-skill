# Team Command Governance (Mandatory)

This rule applies to **every role** in this AI team.

This branch is a **dedicated migration team** with a strict 4-node task chain.

## Allowed messaging path (required)

- **Send messages only via** `atwf` wrappers (policy-enforced):
  - Notice (FYI, no reply expected): `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" notice ...`
  - Action (instruction, no immediate ACK): `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" action ...`
  - Reply-needed (must reply): `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" gather ...` / `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" respond ...`
  - Legacy (operator-only): `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" ask` / `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" send` / `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" broadcast` (discouraged; use inbox-backed `notice` / `action`)
- Check hard permissions any time:
  - `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" policy`
  - `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" perms-self`
- Any progress/completion/design conclusion **must** be reported via `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" report-up` (or `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" report-to {{USER_ROLE}}`) to count as “reported”; otherwise the parent may treat it as “not received”.

## Migration team topology (hard constraint)

- The only supported org tree is:
  - `coord` (user-facing root)
    - `task_admin-*` (one per migration task)
      - `migrator-*`
      - `reviewer-*`
      - `regress-*`
- For each task, the chain is **exactly 4 nodes**: `task_admin + migrator + reviewer + regress`.
- Normal communication must stay within the chain:
  - `migrator/reviewer/regress` report to `task_admin` only.
  - `task_admin` reports to `coord` only.

## Shared worktree (hard constraint)

- For each task chain, `task_admin` creates **one shared Git worktree** and sends the absolute `WORKTREE_DIR` to `migrator/reviewer/regress` via `atwf action`.
- `WORKTREE_DIR` is authoritative for the task chain.
  - `task_admin` spawns `migrator/reviewer/regress` with `cwd=WORKTREE_DIR`, so you should already be inside it by default.
  - Always verify with `worktree-check-self`; only `cd <WORKTREE_DIR>` if needed.
- If you lost the path (inside any tmux worker):
  - print expected path: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" worktree-path-self`
  - verify cwd: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" worktree-check-self`
- Concurrency rule:
  - only `migrator` may modify/commit code inside `WORKTREE_DIR`
  - `reviewer` and `regress` are read-only (diff/test only; never edit files; never commit)
- Only `task_admin` may create the shared worktree (via `atwf worktree-create-self` / `worktree-create`). Child roles must NOT create worktrees.

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
- For `kind=send|broadcast|bootstrap|task`: keep replies minimal (ACK + any required action only).

## Inbox-backed message bodies (mandatory)

To reduce token waste and avoid “confirm storms”, **the full message body is written to a file** and the chat message only contains a short `[INBOX] id=...` notification.

Hard rules:
- For every received `id`, load the full body via: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" inbox-open <id>`
- After you have fully processed the message, mark it read via: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" inbox-ack <id>`
- To see your pending unread queue: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" inbox`
- When you send many messages to the same target, check backlog first: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" inbox-pending <target>`

## Message intents (hard protocol) (mandatory)

All cross-role messages MUST be one of:

1) `notice` (notification / FYI)
- Sender uses: `atwf notice ...`
- Receiver MUST: `inbox-open` → read → `inbox-ack`.
- Receiver MUST NOT: reply upward with “received/ok/ack” via `report-up`/`action`/`ask`.

2) `reply-needed` (explicit answer required)
- Sender uses: `atwf gather ...`
- Receiver MUST use: `atwf respond ...` (or `--blocked --snooze --waiting-on ...`).

3) `action` (instruction / task)
- Sender uses: `atwf action ...`
- Receiver MUST NOT: send an immediate ACK message.
- Receiver MUST: execute; when done, report deliverables/evidence via `report-up` (or `report-to`).

## Notice receipts (replace ACK storms) (mandatory)

To confirm “everyone read the notice”, do NOT ask for ACK replies.
Instead, query receipts by `msg_id`:

- `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" receipts <msg_id>`

Statuses:
- `unread` / `overflow` / `read` / `missing`

## Reply-needed requests (gather/respond) (mandatory)

Use this to collect multiple replies without spamming chat threads.

Hard rules:
- If you need multiple people to reply, use `atwf gather` (not N separate `ask` + repeated summaries).
  - Initiator: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" gather <a> <b> ... --topic "..." --message "<request>"` (or via stdin)
- If you receive a `kind=reply-needed` inbox message, do **NOT** reply via `send/ask`.
  Use `atwf respond` so the system can consolidate:
  - Reply: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" respond <req-id> "<your reply>"`
  - Blocked/snooze: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" respond <req-id> --blocked --snooze 15m --waiting-on <base> "why blocked"`
  - List your pending reply-needed: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" reply-needed`
- The initiator receives **one** consolidated result message (inbox-only) when all targets replied or the request times out.
- If you see a `REPLY wake:` prompt, run `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" reply-needed` immediately and handle the oldest due request.

## CLI injection policy (mandatory)

Goal: avoid spending tokens by injecting prompts into other workers' Codex CLIs.

Hard rules:
- Default delivery is **inbox-only**: `atwf notice` / `atwf action` / `atwf report-*` write to inbox and do **not** inject into the recipient CLI.
- The **only** routine CLI injection is the operator-side watcher `atwf watch-idle`, which:
  - wakes `idle` workers when inbox has pending items
  - injects governance alerts to `coord` when `working` workers ignore inbox too long
  - may auto-send an `Enter` keystroke when an approval menu is detected (config: `team.state.auto_enter`)
- `--notify` / `--wait` are **operator-only exceptions** and should not be used during normal work.

## Agent state protocol (mandatory)

State is **watcher-derived** (operator sidecar `atwf watch-idle`) to reduce token waste and avoid relying on workers to self-report.

Derived states (informational; enforced by watcher):
- `working`: watcher sees recent tmux output activity, or the worker is within a short grace period after a wake prompt.
- `idle`: watcher sees no recent output activity.

Commands:
- View your derived state: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" state-self`
- View others: `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" state <target>`

Hard rules:
- If you are actively working (or after any wake prompt): run `bash "$(git rev-parse --git-common-dir)/../.codex/skills/ai-team-workflow/scripts/atwf" inbox` at least once per minute (or before any blocking wait). If it lists unread `id`s, you must `inbox-open` + process + `inbox-ack` them, then continue work.
- Do **NOT** use `state-set-self` as part of normal workflow; it will be overwritten by the watcher and is only for debugging.
- When you have nothing actionable: simply wait. If new inbox arrives, the watcher will wake you with a short prompt.

## Drive loop (mandatory)

To prevent “stalled work with nobody driving”, the team uses a human-controlled drive loop.

Hard rules:
- `team.drive.mode` is USER/OPERATOR-ONLY configuration.
- Any worker (including `coord`) MUST NOT edit: `.codex/skills/ai-team-workflow/scripts/atwf_config.yaml`.
- For this migration team, `[DRIVE]` is evaluated **per task chain** (one chain per `task_admin-*`):
  - chain must be complete: `task_admin + migrator + reviewer + regress` (exactly 4 nodes)
  - and the chain must be stalled: all 4 nodes `idle` + all 4 inboxes empty
  - the `[DRIVE]` ticket is delivered to the corresponding `task_admin-*` (not to `coord`).
- On `[DRIVE]`, the task_admin’s only job is:
  - diagnose the root cause (run: `atwf state`, `atwf list`, `atwf inbox`), and
  - re-drive work by assigning `action` tasks (owners + next action + ETA) or presenting concrete blocker evidence.

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

- Ask your **task_admin** first and wait for approval.
- If the exception impacts multiple tasks or team governance, escalate to **coord**.

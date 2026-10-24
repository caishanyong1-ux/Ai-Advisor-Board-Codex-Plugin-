# Setup, recovery and project boundaries

## Google setup

`board_setup(user_request=<the human's actual request>)` opens a local page. The user completes Google sign-in, creates/selects a dedicated project, links billing and enables the API/needed partner models in the official console. Credentials, authorization codes, terms and payment details belong in the provider's human flow. The local page stores only project IDs. `board_preflight(check_cloud=true)` reads project, billing and API status without inference. A successful check does not establish Claude entitlement, capacity or response quality.

Authentication uses the official gcloud CLI's Application Default Credentials flow in the board's private `gcloud/` configuration directory, separate from the user's normal CLI configuration. Access tokens are refreshed through gcloud and cached briefly in memory. This version does not implement the full SDK ADC discovery chain, service-account keys or workload identity. Credentials are owner-only local files, not Keychain. Never read or return credential contents.

If gcloud is missing, the package's `prepare_vertex.py` installs a checksum-pinned official Apple Silicon archive into the board's private tools directory. It needs Python 3.12+ for extraction and changes no shell profile. Check its recorded version/hash; a changed vendor archive requires verifying the new official release before updating the helper. It performs no login, resource creation or paid call. See the package README for the local command.

Global endpoints may process data outside a fixed region. If a task requires regional residency, stop preparation and propose the needed adapter change; never silently fall back to global or another provider.

## Job state

| State | Meaning and next action |
|---|---|
| `awaiting_approval` | User confirms the displayed local plan; renew an expired link with `board_approval_link` |
| `queued` / `running` | Approved fixed steps; inspect when useful or cancel on request |
| `pausing` / `paused` | New dispatch stopped; accepted requests settle, then resume or propose a changed lineup |
| `reconfiguration_pending` | New lineup awaits human approval; old lineup stays stopped |
| `superseded` | Historical lineup replaced by an approved revision; statements remain saved |
| `waiting_chairman` | Current Codex reads, verifies and continues/records a stage conclusion |
| `waiting_codex_proposal` | Spawn the fresh isolated Codex draft agent with its blind packet, then submit the real output/agent ID |
| `waiting_codex_review` | Spawn a different fresh isolated Codex reviewer with its blind packet, then submit the real output/agent ID |
| `needs_attention` | Budget, stale price, incomplete output or provider failure; inspect saved results |
| `interrupted` | Worker stopped during unfinished work; inspect uncertain calls before retry |
| `completed` | This agenda's current conclusion is saved; the board project stays open, fresh questions use `board_followup` |
| `cancelled` | New steps stopped; fresh proposal needed for new work |

`board_resume` only creates a human confirmation link. After confirmation it reuses successful steps and frozen anonymous maps, and retries unfinished steps under the same plan. Uncertain earlier requests may already be billed and keep reservations. Exhausted budget, expired/increased prices, changed settings or harness require a new proposal. Diagnose before retrying. Partial results may support a qualified chairman-only explanation; mandatory missing/invalid reviews prevent recording a completed board synthesis.

Version 1.3.2 changes language and stage contracts. Historical meetings remain readable and finalizable when all original ballots validate, but their old approvals cannot dispatch under the new protocol. The older transport/capacity/background migrations are retained only behind their original build gates; they are not an upgrade path to 1.3.2. Preserve old attempts and approvals, and propose new work separately when needed.

Version 1.3.4 provides one audited update for a 1.3.3 first round at rest before any review. `board_resume` offers approval of the Vertex chat shared-context output rule and a new execution fingerprint. It retains complete inputs, requested output maxima, reasoning settings, completed drafts, frozen Codex assignments, and costs. Effective output limits are calculated per request and disclosed on the approval page; the original approval is archived. A pending confirmation becomes invalid if another result commits; request a fresh link rather than altering the stored record.

For HTTP errors, inspect `attempts[].failure` and `request_limits` alongside the saved message. Diagnostics retain known status/parameter vocabulary in order, redact other text and numbers, and hash request IDs. Treat them as filtered diagnostic evidence, not verbatim provider prose or a raw response dump. Effective numeric request settings are recorded separately. A 400 alone does not prove a context, access, or quota failure. Earlier discarded error bodies cannot be reconstructed. Every new generation still needs human retry confirmation.

Codex canonical agent names are scoped to the original task's conversation. The plugin derives that namespace from its saved task binding and returns it with the assignment; submit the tool's unchanged real agent ID and unedited answer. Different conversations may use the same name. Within one conversation, proposals, reviews, rounds, and retries still require fresh agents. An existing uncommitted answer rejected only by the old global-name collision can be submitted against its original assignment after updating. Do not invent an ID, rewrite the answer, or claim an agent ran again. Unlinked legacy jobs retain project-scoped identity checks.

New Gemini 4 Argon global requests persist one create intent and then the returned cloud ID on the existing attempt. `board_status.background_requests` exposes lifecycle metadata only. `board_recover_background` authorizes the project and frozen target, retrieves the same ID, reconciles final usage, and deletes the remote resource after the receipt is durable. It creates no new attempt or generation and starts no subsequent paid stage. Read-only polling can retry temporary failures. Retrieval and cancellation still work after tariff expiry or a runtime update; promotion to a ballot additionally requires the approved validator. An `awaiting_validation` receipt is retained and may be validated locally after compatibility is restored, without another model call.

If create returned no ID, or GET reports that the saved resource is unavailable, keep its unknown cost. A new paid attempt needs a fresh human `board_resume` confirmation explicitly retaining that reservation. A 404 does not prove no work occurred, successful cancellation, deletion, or zero billing. Never list unrelated cloud interactions to guess which work belongs to the task.

`board_cancel` revokes unused approval links and stops new dispatch. For saved background IDs it requests remote cancellation and retrieves terminal status/usage; failed confirmation remains visible for recovery. Closing a streamed connection alone cannot recall an accepted request. Cancellation remains terminal if a completed answer races with it. Pausing allows accepted work to settle and preserves its answer. The original deadline is retained across restart; recovery after expiry attempts remote cancellation before reconciliation. Saved answers precede deletion; pending deletion is independent of answer validity and billing. Legacy OpenRouter history remains readable/cancellable, but its approvals cannot execute after this migration.

## Invalid ballot schema

Version 1.3.2 supports a human-approved schema correction/completion for first-round Codex-chaired meetings with the exact current frozen contract. Older builds require their original compatible runtime; do not rewrite their contract to bypass that boundary. Inspect the saved response before calling `board_resume`. A supported completed review has unambiguous integer scores/ranking but missing criterion reasons or issue arrays nested under `criteria`. The worker prepares an exact correction packet using only that reviewer's original anonymous prompt, own visible response and schema errors. Original scores, ranking, narrative, existing reasons and issue/uncertainty strings are immutable; missing reasons are new content authored by the same reviewer. This is not a reassessment or a vote supplied by the chairman.

The human submits the correction page. Each listed slot gets one atomically claimed paid attempt; a failed correction requires another human approval. The larger packet receives a fresh reservation within the same total budget. Preserve previous responses/costs and valid peer reviews. Never manually normalize a live ballot, edit real approval/attempt rows, include other reviews or substitute an author. All earlier background operations must be reconciled and deleted before this plan migration. Ambiguous judgments require a separate proposal, not guessed values. The unchanged strict validator and preservation checks must both pass; then the worker stops at `waiting_chairman`.

## Current status and exported handoffs

Use `board_status.current_status` as the current status source: it includes the check time, job update time, meeting state, saved-conclusion flag, originating board-project state, approved replacement job IDs and next action. Meeting synthesis, a user-selected substantive plan, and project closure are separate milestones; do not infer either later milestone from `completed`.

When exporting a handoff, keep one current `START_HERE.md` index with the status timestamp, project/job IDs, actual state, next step and links to evidence. Keep immutable snapshots/manifests under dated paths and label earlier status descriptions as historical/superseded. The current index is mutable and stays outside immutable snapshot hashes. Do not rewrite or rescore old evidence to reconcile stale summaries.

## Continuity

For another local chat, establish the actual workspace, bind it and use the user-specified job ID. `board_list` exposes only that project's metadata; resolve ambiguity before loading other job content. Never infer the active project from the plugin working directory or auto-load unrelated histories.

`board_session` stores an explicit (project, conversation ID) binding independently of job completion. See [follow-ups.md](follow-ups.md) for explanation versus substantive follow-up routing, fresh approvals and history summaries. Session inspection returns only that binding's metadata. For membership/workflow changes to an unfinished agenda, use the pause/reconfiguration process in [membership-changes.md](membership-changes.md).

Scopes check each job's stored owner; symlinks resolve to a canonical path. This is application-level separation for trusted local Codex, not an OS sandbox against another process with the same macOS account. The local approval page similarly prevents accidental tool approval and cross-origin submission, not malicious same-user code. The skill must never automate a real approval.

State is outside the plugin cache at `~/Library/Application Support/AI Advisory Board Codex/`. It contains approved packets, visible member answers, reviews, evidence, chairman decisions and checkpoints. Hidden reasoning is discarded; no shared semantic memory or cross-project retrieval exists. Updates preserve the state and old keys, but the Vertex runtime never uses OpenRouter credentials.

The worker survives MCP client closure while the Mac is awake and connected. It saves and waits for Codex decisions; it does not keep the current Codex reasoning in an inactive chat. After reboot the next tool call restarts it and reconciles interrupted work. There is no login daemon, automatic wake-up, cloud executor or cross-machine synchronization. Notifications require a separately requested app automation.

## Only the user closes the board project

A job reaching `completed` records a round/agenda result; it is never a case closure. `board_session(action="end")` disables board mode without closing the project. Only a direct explicit user instruction permits `board_task_lifecycle(action="close", user_statement=...)`; record or cancel unfinished agendas first. Saved sources, attempts and conclusions remain available. Reopen through the lifecycle tool only when the user requests it. Neither silence nor a successful deliverable supplies closure authorization.

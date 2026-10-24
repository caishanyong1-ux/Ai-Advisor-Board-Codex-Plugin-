# AI Advisory Board 1.3.4 — Codex plugin

AI Advisory Board brings structured multi-model discussion into Codex. Use it to compare independent approaches to a research question, software design, or complex decision, then turn those perspectives into a clear, evidence-aware recommendation. It adapts the workflow of [HaroldZhong's AI Advisory Board](https://github.com/HaroldZhong/ai-advisory-board) into a local Codex plugin.

Codex chairs the meeting: it clarifies the task, prepares selected evidence, recommends members, verifies claims, synthesizes the discussion, and carries out authorized work. A separate Codex participant and at least four external models provide independent answers, followed by anonymous peer review. The user approves the workflow, models, data scope, and costs before paid work begins.

- **Compare perspectives:** independent drafts, a shared review rubric, and access to each member's original answer.
- **Keep control:** explicit approval for model calls, disclosed cost estimates, and no automatic paid retry or model substitution.
- **Continue with context:** local meeting records, selected source snapshots, project separation, and resumable workflows.
- **Bring your own connections:** supported Vertex AI and compatible API routes; no credentials, account settings, or meeting records are bundled.

Only the user can close the board project. Model agreement is advisory and still requires verification.

## What's new in 1.3.4

- **Vertex chat capacity:** DeepSeek and other Vertex chat routes reserve room for the complete input before setting the output parameter. Highest keeps the requested model maximum and uses the remaining shared context; reasoning effort stays fixed. The approval page discloses the conservative byte-based calculation, and every attempt records its effective limit. Inputs and peer answers are never silently truncated.
- **Actionable HTTP failures:** Google HTTP errors retain their status and bounded, sanitized parameter diagnostics. Raw bodies, request text, credentials, and arbitrary debug details are not stored. Failed calls do not retry automatically.
- **Preserved work:** an audited 1.3.3 first round, at rest before review, can request a new approval for the capacity rule. Completed answers, independent Codex packets, prior costs, and the original approval remain intact. This is not a general migration of arbitrary old workflows.
- **Codex identity:** canonical agent names are scoped to the original Codex conversation, preventing collisions between unrelated chats. Fresh-agent checks still apply across all stages and agendas within that conversation. Previously uncommitted answers keep their real identity and original assignment.

## What's new in 1.3.3

- **Conversation language:** Codex sets a presentation preference per project and conversation. User-facing reports, selection controls, approval pages, and API/Vertex setup follow that preference. Explicit user preferences take precedence; source text and brief code-switches do not automatically change it.
- **Interface translations:** English, Simplified Chinese, and Traditional Chinese are bundled. For other languages, the current Codex translates a complete versioned interface template locally before opening forms. Markup, links, form fields, and ordered placeholders must remain intact. This uses the current assistant, with no additional external model request. Translation quality still needs the assistant's judgment.
- **Independent language settings:** changing presentation language does not rewrite approved plans, internal English drafts/reviews, rankings, original answers, or task-specific artifact requirements. Technical/source records retain their recorded language. No browser-language or machine-wide preference is used.

## What's new in 1.3.2

- **Stage contracts:** independent members and the chairman have separate deliverables and success criteria. Every member, including independent Codex, receives the same member contract. Peer review evaluates that contract rather than penalizing missing chairman work.
- **Language:** internal discussion, analysis, peer review, score reasons, and chairman synthesis use American English. User-facing reports, chat, and functional forms follow the current Codex conversation language, independently of internal work or the artifact language. Formal task outputs follow the task-specific `deliverable_language`; source quotations and code retain their original form.
- **Faithful anonymous references:** each review stays unchanged in its local label namespace, alongside an explicit local-to-Stage-1/model map. Only validated structured scores and rankings are mapped for aggregation. Bare letters, possessives, tables, and formulas are never rewritten speculatively.
- **Research review:** distinguish supported facts, proposed designs, assumptions, and unconfirmed numbers. Apply relevant estimand, outcome, mediation, fallback, feasibility, and conditional-recommendation checks within the existing scoring criteria. Useful recommendations earn credit; adding generic caveats does not.
- **Chairman evidence:** dated evidence found after voting is recorded separately as `chairman_only`. It is not attributed to member consensus and never silently changes old ballots.
- **Inspect independent answers:** result messages offer access to each member's original answer, including independent Codex. The result index points to complete paginated originals, with completion status; translations and summaries are labeled separately. Blind-stage visibility gates still apply.
- **Current status:** distinguish a saved meeting conclusion from the user's substantive decision and project closure. Timestamped current status supports a single handoff entry while historical snapshots remain intact.

These prompt changes apply to newly approved meetings. Historical results remain readable; completed valid ballots can still support chairman synthesis. Old approvals cannot launch paid work under the new protocol. Preserve them as history and prepare a new proposal when new work is needed.

## Editions

| | Public | Personal |
|---|---|---|
| Claude | Full Vertex Claude adapters retained | Adapters/history retained; Anthropic excluded from recommendations and execution |
| Vertex | Each user connects their own Google account and Cloud project | Uses the owner's existing local connection |
| Grok | User adds xAI or uses an enabled Vertex route | Can reuse the existing local Grok connection directly |
| Other APIs | Add, edit, enable, disable, or remove | Same |
| Packaged user data | No credentials, account configuration, or meeting records | Same; installation reads separate private local state |

The editions are alternative installations of the same plugin. `edition.json` determines defaults. Building a public ZIP does not publish a marketplace listing. The plugin is available under the [MIT License](LICENSE); upstream-derived portions retain their original MIT notices in [NOTICE.md](NOTICE.md) and [the upstream license](licenses/ai-advisory-board-MIT.txt).

## Installation and setup

Requirements: macOS, Codex desktop with the `codex` CLI available, and Python 3.9+. From the extracted package:

```sh
python3 install.py
```

The installer backs up the existing plugin and personal marketplace while preserving other plugins. Let active requests settle before updating; installation does not terminate them. If a different worker version is running, ask Codex to inspect and back up its state before restarting it. A new local chat can load the updated plugin tools. Select **AI Advisory Board**, or invoke `$board-with-codex`.

For Vertex, ask Codex to open the board's Vertex setup. Complete Google sign-in, project selection, billing, and any required model terms yourself. The connection check is read-only and does not verify model entitlement or inference capacity. Authentication uses gcloud Application Default Credentials in the board's private configuration directory, not the full SDK credential-discovery chain.

If gcloud is missing, the Apple Silicon helper can download and verify a pinned official CLI archive:

```sh
python3 prepare_vertex.py --python /absolute/path/to/python3
```

The extraction interpreter must be Python 3.12+. Intel Macs should use an existing official gcloud installation. The helper changes no shell profile and performs no login or paid call.

For another API, explicitly ask to add or manage a model connection. The private form asks only for a name, base URL, and key. Codex handles model discovery, supported parameters, and verified prices. Presets cover common providers and local Chat Completions-compatible services; arbitrary protocols are not automatically supported. Native Anthropic direct APIs require a different adapter; use Vertex Claude or an explicitly compatible service. Missing or stale prices block paid proposals.

Enter secrets only in the private form. Credentials use owner-only files (directory mode 0700, file mode 0600), not macOS Keychain. Personal Grok can reuse the existing local xAI credential without copying it. Public installs do not automatically reuse that personal connection.

## Meeting workflow

1. Bind the actual workspace and chat to a board project. Apply grill-me and writing-for-agents preparation: inspect discoverable facts, carry settled choices, and ask only consequential unresolved questions.
2. Capture selected source files/ranges with specific purposes. Each range gets a citation ID; an ID does not prove that every attached claim is supported. Members receive approved text snapshots, not filesystem or browser access.
3. Choose **Highest**, **Balanced**, or **Economy**. Codex recommends a level based on task difficulty; the user chooses. The level is a meeting strategy, distinct from each provider's reasoning setting and the host Codex configuration.
4. Review the complete accessible model inventory, current independent benchmarks, and official availability, retirement, parameters, and prices. A formal board has **Codex plus 4–8 distinct external models**, selected for complementary capabilities. Multiple models from one maker are allowed; role, effort, and route aliases do not count as additional models.
5. Compare equivalent routes only among configured usable connections. Inaccessible and retired models do not enter selection; a new leaderboard entry never triggers API setup by itself. The compact message shows the recommended lineup and **Use / Adjust / Details**. Costs and reasons are available in Details.
6. Freeze member and chairman contracts, language, workflow, models, sources, rubric, settings, rounds, and budget. The human submits the local approval page. Viewing details or choosing a lineup is not execution approval; the agent never submits this form for the user.
7. Run **independent answers → anonymous peer review, shared scoring, and ranking → chairman synthesis**. A fresh isolated Codex agent drafts, and a different fresh isolated Codex agent reviews. All drafts must commit before review; all valid reviews must commit before synthesis. The current chairman does not cast another vote.
8. Report substantive agreement, disagreement, adopted insights, and next steps. Numerical scoring stays in details. Offer each member's original answer on request. Save the meeting conclusion without closing the project; substantive follow-ups receive a new proposal and approval.

The original rank mathematics and `FINAL RANKING` format remain. A shared 0–5 rubric produces weighted /100 judgments with spread and leave-self-out diagnostics. These are not correctness probabilities or universal model rankings. Research writing rewards clear, evidence-matched contributions while preserving material null results and necessary limitations. Engineering rewards proportionate complexity while retaining required tests, error handling, integrity, and safeguards. See [deliberation rules](skills/board-with-codex/references/deliberation.md).

A round with four external members uses four draft calls and four review calls; each reviewer sees all five answers. Two isolated Codex stages use the host account, separately from API costs. Bounded requests run concurrently. Services without a verified total reasoning cap retain the existing sequential cost gate and still receive no other reviewer's output.

## Changes, costs, and recovery

To change members mid-meeting, ask to pause and refresh recommendations. Accepted calls settle first. A newly approved revision preserves prior contributions and costs; recommendations never change an approved lineup automatically.

Highest uses verified model output capacity by default; Balanced/Economy use task-adjustable output allowances. All levels preserve complete approved evidence and peer answers. Provider context/output limits, deadlines, and a separately approved monetary budget still apply. UTF-8 byte bounds are conservative cost estimates, not measured tokens. Long-context pricing coverage must be verified.

Grok's output parameter limits visible output rather than total reasoning. Its dispatch estimate is not a guaranteed billing cap. The confirmation page discloses this and requires explicit acknowledgment; such requests reserve remaining funds and reconcile returned usage before continuing. Unknown usage retains its reservation. There is no automatic retry, budget increase, or model substitution.

The worker saves progress while the Mac is awake and connected. Supported Gemini background requests persist a cloud ID and use the disclosed Preview storage flow. Recovery retrieves that same operation, persists its terminal receipt, and requests remote deletion; it never creates a replacement generation. Other routes checkpoint visible streamed text. Partial output is not a valid ballot. A lost create response without an ID is never automatically resent. The original deadline remains in force across restart.

Supported invalid review schemas can receive one separately approved correction per listed reviewer under the exact current contract. The reviewer sees only its frozen anonymous packet, own prior response, and schema errors. Scores, ranking, existing commentary, reasons, issues, and uncertainties stay fixed. Only that reviewer supplies missing reasons. Strict validation and preservation checks remain mandatory; failures require fresh approval. Older build-specific migrations do not bypass the 1.3.2 protocol boundary.

Private state lives under `~/Library/Application Support/AI Advisory Board Codex/`, outside the plugin cache. It contains approved packets, visible answers, reviews, approvals, costs, and saved decisions. Hidden reasoning is not stored. Project/chat separation is application-level isolation, not an OS sandbox against processes using the same account. Cross-chat continuation requires explicit selection; there is no cross-project semantic memory. The worker does not keep an inactive Codex chat thinking, wake the Mac, or send automatic notifications.

## Validation

```sh
python3 -B -m unittest discover -s tests -v
```

Tests use isolated directories, synthetic data, fake providers, and local loopback servers. They make no paid inference calls. `VALIDATION.json` records this release's checks and their limits; `RELEASE-MANIFEST.json` records packaged file hashes. Successful mock routing verifies protocol delivery, not how reliably a real model will follow a new prompt.

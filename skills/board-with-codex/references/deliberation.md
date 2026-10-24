# Deliberation contract

Read this when preparing or running a meeting. This plugin improves the original AI Advisory Board three-stage method. Never replace a board with a single-model review or skip peer scoring to save cost. All three thinking levels use the same mandatory stages.

## Before approval

Use at least four distinct external models plus **one independent Codex participant**. The current Codex remains chairman and has no second vote. Same-maker models are allowed for complementary capabilities; route/role/effort aliases do not create distinct seats.

Choose `rubric_profile` from the deliverable and show it with the compact workflow card:

- `general`: task alignment 25%, correctness/evidence 30%, reasoning 20%, usefulness/actionability 15%, clarity 10%.
- `research_writing`: manuscript text or substantive editorial recommendations. Baseline 20/25/20/10/10 plus non-defensive, evidence-bounded research writing 15%.
- `software_engineering`: development/design/refactoring with implementation decisions. Baseline 20/25/20/10/10 plus proportionate engineering 15%.
- `research_software`: both kinds of deliverable are actually requested. Baseline 15/25/15/10/5 plus each task-specific criterion 15%.

Every criterion uses integer 0–5, concrete reasons and the same anchored definitions for every reviewer. The plan freezes profile, weights, anchors and harness hash before approval. Do not switch profiles after seeing answers. Explain profile choice briefly only if it is ambiguous; use the existing deliverable to decide routine cases.

Both common principles apply: **avoid over-engineering; avoid over-defensive writing**. Research writing makes the strongest defensible contribution explicit, removes repetitive apologies/anticipatory rebuttals, and keeps material null findings, planned outcomes and interpretation-changing limitations. Necessary caveats appear once, precisely, where relevant. This never suppresses valid criticism or licenses selective reporting, fabricated evidence or overclaims. Engineering assesses whether complexity serves the present requirements; necessary tests, error handling, data integrity, security and required scale are not penalties. Ask for specific passages/mechanisms and feasible improvements, not vague style judgments. Do not double-count the same defect across criteria without distinct consequences.

Set `peer_review: true`, `include_codex_member: true`. All live plans require these; disabling is reserved for isolated mock adapter tests. The approval page displays the contract. An old approved job with a different harness cannot acquire new behavior silently; preserve it as history and prepare a new proposal.

## Stage scope and research review

Every member shares the approved English `member_deliverable` and `member_success_criteria`. Reviewers assess those requirements; chairman synthesis and user-facing translation are separate stages. Per-member focus cannot change delivery language or create different scoring obligations.

For research writing/design, the runtime injects the shared requirements from `runtime/task_standards.py` into every member and reviewer packet. Apply relevant estimand, evidence-support, numerical-assumption, fallback, outcome and mediation checks within the existing criteria and weights. Identify concrete defects and useful corrections; a longer limitations list is not inherently better. Reasoned conditional preferences are valid. Preserve necessary null findings, uncertainty and safeguards.

## Independent Codex participation

The worker cannot spawn host agents itself. The calling Codex must coordinate actual isolated subagents after human approval:

1. Immediately obtain `board_codex_packet(stage="proposal")`. While the worker requests external drafts, use the host's `spawn_agent` with **`fork_turns: "none"`** and only the packet's system, prompt and no-tools instruction. Do not add the chairman's preferred solution, parent conversation, model list or other outputs. Use the host's current model by default; do not invent a different model identity.
2. Submit the actual returned agent ID and **unedited visible answer** with `board_submit_codex`, matching assignment ID/hash. Never supply hidden reasoning. Never write an independent answer yourself or fabricate an agent identity. An agent without tools uses the same frozen evidence as external members. Do not give it local files/browser access.
3. Once `board_status.codex_participation.review_packet_ready` is true, get `board_codex_packet(stage="review")` and immediately spawn a **different, fresh, isolated** agent with `fork_turns: "none"`. Supply only the blind packet. Do not reuse the proposer, forward its history, identify its answer, or share external reviews/scores. It reviews every anonymous answer, including the independently generated Codex draft, using the same rubric. Submit its actual ID and unedited answer. Each retry also uses a fresh agent; failed submissions remain in the used-agent history. Exact redelivery of an already committed output is safe and creates no new vote.
4. Keep checking progress at sensible intervals while the independent agent and provider calls run. No paid stage starts before approval. Do not wait for external reviews to inspect their views before starting the isolated reviewer. The worker preserves stage barriers even if the host is late.

If isolated-agent tools are unavailable, explain the missing capability and leave the job waiting; do not silently replace the independent member with the chairman. `waiting_codex_proposal` / `waiting_codex_review` mean the host must provide the isolated contribution. The worker can verify stage/order, frozen payloads and different supplied agent IDs; it cannot independently attest how a host constructed a subagent's context. The host must honor the isolation instructions.

## Anonymous parallel review

All initial drafts must commit before any reviewer receives a packet. Each reviewer gets the same question, evidence, standards and rubric, with all answers in a separately shuffled anonymous order. Label maps are saved before dispatch and reused on retry. Current-round author maps, other reviews and aggregate scores are withheld. No model gets a special reviewer role. Raw answer text can still reveal style or self-recognition; anonymity reduces cues but does not eliminate bias.

For follow-ups, prefer the existing approved `history_summary` when old ballots or rankings would anchor a new assessment. Preserve facts, source provenance, settled constraints, material criticisms, minority views and unresolved issues; omit vote totals and author attribution from the summary. The runtime omits prior answer/model metadata from summary packets, while retaining complete audit records locally. If the user chooses full visible history, disclose that it includes historical reviews and identities, and give the same history to everyone. It is not current evidence or additional votes.

Bounded API calls run concurrently. Providers without a verified total-token cap may be serialized by the existing cost gate; they still receive no other reviewer's output. Codex review can proceed alongside provider review. One review request contains all answers: with four external members, each round schedules four external drafts plus four external review calls, and two Codex agent stages. A separately selected external chairman adds its approved synthesis call. This is not N-squared API requests. Host Codex usage is separate from API cost estimates.

The exact upstream `FINAL RANKING` format remains. The new structured scores block precedes it. Missing, duplicate, unknown or malformed ballots are stored as incomplete and never repaired or inserted into aggregate votes silently. No aggregate result is released for incomplete coverage. A failed member or invalid ballot requires inspection and separate human retry approval; completed steps and fixed label maps are reused. Uncertain charges remain reserved. Pause/cancel/reconfiguration cannot release a new stage without its approval.

## Chairman synthesis

After all valid ballots commit, read **every page** of `board_chairman_packet`; it contains all drafts and full unchanged reviews with per-review local-to-Stage-1 maps, rank statistics, score spread, material issues and self-excluded sensitivity. Read raw answers/maps with `board_result` / `board_read_answer` for auditing. Only validated structured scores/ranks are mapped for aggregation. Raw review prose stays in its own local namespace; use its adjacent map for full or bare-letter references. Until the isolated Codex commitment, ordinary result readers withhold relevant peer text so the host does not accidentally contaminate its input.

Original average-rank and population-variance consensus calculations are preserved and tested against pinned upstream source. Consensus is ranking agreement, not factual correctness. The new /100 number is a weighted judgment index, not probability. Adding a rubric changes elicitation; matching the original formulas does not make experimental results equivalent to the original prompts. Self-reviews retain one equal vote as upstream; an additional leave-self-out diagnostic exposes sensitivity without changing the primary result. Do not delete outliers, change weights, interpret rank tie-breaking as a measured score difference, or automatically choose a winner.

Evaluate evidence, retain useful minority views, resolve material flags and explain which recommendations are accepted or rejected. Scores and rankings can disagree; reason about that disagreement rather than letting either automatically override the other. Record `deliberation_hash` from the synthesis packet and a substantive `peer_review_assessment` with the normal decision/evidence/unresolved/next_steps fields. Only a complete board can be recorded as a board conclusion. For an incomplete board, give a qualified chairman-only status and offer retry/cancel; do not label it completed. The board project remains open until the human explicitly closes it.

Chairman verification after voting belongs in `evidence_delta`, with dated sources, affected claims, findings and `member_visibility: chairman_only`. Never claim members saw it, modify historical ballots, or feed new evidence only to unfinished reviewers. If a new agenda is needed, propose it for approval. Scores and rankings describe this meeting; do not reuse contested votes as general model capability ratings.

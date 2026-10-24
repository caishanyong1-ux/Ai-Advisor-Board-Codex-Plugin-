# Preparing a useful board

The board applies grill-me and writing-for-agents methods as a built-in preparation discipline; separately installed personal skills are optional. Add other Matt Pocock methods only when useful: a compact specification for a substantial build, task slicing for implementation, domain definitions for conflicting terminology, diagnosis for a concrete failure.

| Mode | Member work | Chairman's responsibility |
|---|---|---|
| `explore` | Independent options, evidence, objections | Select or combine approaches with explicit tradeoffs |
| `solve` | Alternative explanations, discriminating tests, predictions | Verify with Codex tools and return findings within scope |
| `produce` | Drafts, designs or implementation proposals | Produce and check the deliverable |
| `custom` | Task-specific roles fitting the supported stages | Explain the arrangement and obtain approval |

The runtime requires independent proposals, anonymized peer scoring/ranking, then chairman synthesis. An isolated Codex participant answers and a different fresh isolated Codex agent reviews alongside external members; the current chairman does not vote again. Follow [deliberation.md](deliberation.md). Later approved rounds receive consolidated findings. Independent members have no tools/files/grounding/execution privileges; **the chairman** supplies approved evidence and performs authorized work. An external chairman performs one synthesis after the current round; another externally chaired run needs a new proposal.

The first common packet contains facts, assumptions, constraints and open questions, without presenting the chairman's preferred answer as the conclusion. Every member independently assesses the same complete problem. Capability complementarity comes from the selected models, not assigning one model only writing and another only criticism. Specialized assignments are optional only when the user requests them. External questions return to Codex for consolidation.

## Select for value

Follow [models-and-providers.md](models-and-providers.md) for fresh research within existing access, exact settings and cost semantics. Use the returned selection ID, not an untracked cached ranking.

A formal board requires Codex plus 4–8 distinct external models. Different models from one manufacturer are allowed when their capabilities complement; changing the role, effort or API route of one model does not add a seat. Record evidence for capability differences and limitations without claiming domain expertise or independent errors from brand names. Use only enabled configured usable routes. If too few suitable models are available, report the shortage and leave the meeting pending; API setup starts only on a separate explicit human request. Never silently shrink the board.

Apply the human's thinking level: highest prioritizes capability without economizing at its expense; balanced weighs capability, full cost and likely rework; economy favors value while still meeting the task's quality requirements and minimum board size. Compare equivalent offerings only among already connected usable routes. Price does not replace capability selection. Include every proposal, review, approved round and external chairman call; Codex usage is outside this API estimate. Keep reasoning in the selection report, disclosed when requested.

Use `board_value_summary` only for relevant jobs in this bound project. It reports costs and chairman-assessed outcomes, not a universal model ranking. Sparse or unlike tasks do not justify a measured quality-per-dollar claim. State the evidence gap.

## Proposal contract

The tool schema is authoritative. Live plans use `provider: "live"` for mixed providers, or `"vertex"` for Vertex only, plus a fresh `selection_id` and matching `thinking_level` (`highest`, `balanced`, `economy`). Each member needs its role, rationale, **value_rationale**, complete assignment, supported effort and output cap. Keep `chairman: "codex"` unless the user explicitly selects an external chairman. The meeting level is not a literal provider effort value; use verified model-specific settings. Do not claim to change the current Codex host's configured model/effort.

`value_case` requires:

- `quality_gate`: observable acceptance criteria.
- `alternative` and `tradeoff`: a suitable alternative and its capability, cost, latency and rework tradeoffs under the selected level; it need not be the cheapest.
- `member_count_reason`: concrete capability complementarity from at least four distinct external models.
- `cost_assumptions`, `expected_cost_low_usd`, `expected_cost_high_usd`: plausible whole-task range, with input/output, review and round assumptions.
- `stop_condition`: when more spending would not materially improve the result.

Supply the goal, English `member_deliverable` and `member_success_criteria`, chairman `deliverable` and `success_criteria`, task-specific `deliverable_language`, neutral English brief, data scope, workflow/reason, dated `model_evidence`, rounds, `peer_review: true`, `include_codex_member: true`, task-appropriate `rubric_profile`, timeout, `feedback_max_chars` and `max_cost_usd`. The runtime freezes the harness/rubric and each member's target, credentials version, settings and price snapshot. A changed setup cannot redirect an existing approval. Whole-meeting cost includes every external draft and review, all approved rounds, and a selected external chairman; independent Codex usage is separate.

The initial packet and bounded same-scope chairman findings are authorized together. Extra source material or a changed purpose outside the described scope requires a new approved proposal. Local source capture reads only explicitly selected files/ranges in this task workspace. Follow-ups retrieve only explicitly linked same-project history; neither action uploads until its plan is approved. State exactly what text leaves the Mac, including sensitivity where relevant.

The approval page shows the expected range and independently computed first-round estimate. See the provider reference for Grok's variable reasoning costs and mandatory acknowledgement. For bounded adapters, insufficient remaining budget stops dispatch and saves partial work; missing usage, timeout and uncertain responses retain reservations. A truncated answer is saved as incomplete and does not feed automatic peer review.

Each proposal records the deliverable, workflow, complementary models, alternative arrangement, evidence, limitations, data destination, decision maker, expected cost and stop rule. Default chat remains compact; detailed selection reasoning is available on request.

Member deliverables must stand alone: include the requested memo, analysis or draft and its acceptance criteria, without requiring the final chairman synthesis. All independent seats use the same scope, including Codex. For research evidence, capture separate file ranges for distinct findings and identify what each range supports. A citation ID is not proof of entailment; check numerical proposals against the excerpt or label them as assumptions/candidate values.

# Follow-ups after a board conclusion

## Choose the branch

1. Bind the current task workspace and inspect `board_session` with this chat's conversation ID. Read the referenced job's complete result and chairman conclusion. If no session exists, attach the explicitly identified prior job; resolve genuine ambiguity before loading unrelated history.
2. **Explanation**: the user asks what a saved result means, why a recorded decision was made, or for status. Answer from saved evidence with `Chairman explanation | No new model consultation`. State when the archive does not support an answer. A new inference that could change the decision goes to the substantive branch.
3. **Substantive follow-up**: new evidence, constraints, alternatives, a challenge to the conclusion, or a request to develop the work further. Default to a board continuation, even when the user does not repeat “board.” State the proposed task and what fresh member input would add. As chairman, judge whether fresh model input adds enough value. Propose a meeting when it does; otherwise explain the reason and carry out authorized work as chairman-only. If the user explicitly requests the board, prepare that meeting rather than silently downgrading it.
4. If the job is still `waiting_chairman`, `board_continue` may use another already-approved round only within its scope, lineup, settings, budget and limits. For a mid-meeting lineup/workflow change, follow [membership-changes.md](membership-changes.md). Otherwise record the current agenda conclusion (including limitations; this does not close the project) and call `board_followup` with the prior job ID, current conversation ID and a fresh complete plan. Changed scope or a completed job always requires a fresh human approval. A running or unresolved request must settle before history is snapshotted.
5. Present `Continuation pending approval`, the new workflow, model roles/settings, cost rationale and local confirmation link. Stop before paid execution until the human submits it. Preparation must produce no model calls. Then chair and deliver through the main skill.

For ordinary execution between meetings, Codex can inspect files, implement approved work and test results within the user's task authorization. Label this as **Chairman execution | No new model consultation** when reporting it as part of board work. Submitting member requests always follows approval.

## Carry evidence without inventing memory

`board_followup` follows only explicit same-project ancestry. By default it includes prior task packets, round feedback, visible answers with failure/incomplete labels, and chairman conclusions. Members receive these as historical evidence, while their current-round proposals remain independent. All history is frozen into the new approval and counted in the input cost bound. It is not a provider-side chat session.

If the 60KB history bound is exceeded, read the original answers with pagination and supply `history_summary`. Preserve original constraints, changed constraints, supporting evidence, unresolved disagreements, failed/missing contributions and decisions still open to revision. The approval page labels this as a chairman-written summary and lists all source jobs and snapshot hashes. Raw history stays in the archive; nothing is silently truncated. Summaries are advisory evidence, not a new consensus.

After `board_record_conclusion`, the chat binding stays active. End it only when the user explicitly leaves board mode. A changed agenda remains in this task's board project. A new chat requires explicit attachment; project membership alone does not select a prior board. Keep the job ID and project path in any handoff.

## Control boundary

The runtime enforces project access, immutable history, approval, price/budget bounds and saved provenance. The skill routes natural-language messages and labels replies. There is no Codex-wide message interception hook; do not promise every future reply is mechanically forced through the board. If tools are unavailable, say so and retain the pending board request rather than presenting a chairman-only answer as fresh board advice.

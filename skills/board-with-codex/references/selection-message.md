# Choose thinking level, then the meeting plan

Use `request_user_input_async` for these preference rounds when available; otherwise ask in the final response. Carry settled choices forward. A clear existing choice needs no repeated question. Continue independent preparation while waiting; elapsed time is not an answer.

## Thinking-level grilling round

After clarifying the goal and before selecting models, assess task difficulty: reasoning depth, conflicting evidence, uncertainty, context size, consequences of errors and required output quality. Recommend one level with a brief task-specific explanation; put it first and label it `(Recommended)`. The human chooses:

- **Highest (`highest`)**: capability first; no additional application token allowance. Verify current endpoint capacity and supported reasoning settings; omit `max_output_tokens` to use its configured verified maximum. An explicit smaller output override requires the human's choice. Still compare equivalent routes and disclose the complete proposed cost.
- **Balanced (`balanced`)**: combine capability, whole-task cost, latency and expected rework.
- **Economy (`economy`)**: emphasize value while still meeting the task's quality requirements.

Every level retains **Codex plus at least four distinct external models**. Do not turn economy into a single-model consultation. This is a meeting strategy, not a literal API reasoning parameter. After the choice, map it to each selected model's verified effort/output settings. The current Codex host's model/effort is not changed by this field. Save the chosen level and difficulty assessment in the selection research and plan. Revisit it only if the user changes it or the task materially changes.

Preserve the complete approved evidence and all peer answers at every level. Context capacity is a model property, independent of the cost preference; never shorten or drop an answer to fit a cheaper mode. Balanced/economy default output allowances are 16,384/8,192, bounded by the endpoint capacity and adjustable to the actual task before approval. Highest defaults to the verified endpoint maximum; refresh stale capacity metadata instead of reusing a universal 32K cap. Provider context, supported single-request output, timeout and the separate approved monetary budget still apply. Large decode protocols requiring additional requests need their own supported adapter and approval.

For Vertex chat routes, output shares the context window with input. The approved rule uses `min(requested maximum, context capacity - full wire-message UTF-8 byte estimate - 4096 framing reserve)` and recalculates it for every complete packet. Highest retains the verified requested maximum and reasoning effort; the effective output limit uses remaining capacity. Byte bounds are conservative estimates, not measured tokens or proof that input overflows. If the estimate leaves no verified headroom, obtain verified counts or propose an explicitly approved input change; keep all materials intact until then. Verify `input_token_limit` pricing coverage and inclusive `tiers.from_input_tokens` boundaries for the full model context. Older quotes with only 100K coverage need refreshed evidence; never silently extrapolate a short-context tariff. Show capacity, overrides and costs in details/the confirmation page, keeping the default lineup message concise.

## Lineup-only choice

Research capability combinations among enabled, configured and usable routes. Save costs, evidence and reasoning in the selection record. The default chat message is only:

> Recommended board: Codex (chairman + independent seat), Model A, Model B, Model C, Model D.
>
> Use recommended lineup / Adjust models / Details

Use the actual exact model names. For the choices use `request_user_input_async` when available, with short neutral descriptions. Keep fees, justifications, unselected lists, workflow explanations and technical settings out of the initial message and choice descriptions. Alternative lineups must each meet the minimum size. Unconnected services are outside this meeting's candidate pool; only an explicit human request enters [API setup](api-connections.md).

If the user chooses **Details** (or asks for costs/reasons), read `board_selection_details` and send one organized response covering the whole-meeting estimate and proposed cap, assumptions and material cost uncertainty, selected level, workflow/rounds/rubric, source scope, models/routes/settings, capability complementarity and selection reasons. Account for every considered available model; unselected ones are marked **Not selected**, with reasons in this detailed response. Include equivalent-price route comparisons only for already connected routes. Exclude inaccessible and retired models. Viewing details leaves the choice pending and makes no paid call. Do not confuse a prior recommendation with the user's eventual chosen lineup.

After the user selects the arrangement, freeze it with `board_prepare` and show the local approval link without repeating its costs/reasons in chat. The human form retains the exact level, data, models, workflow and budget before billing. Chat selection does not replace this runtime confirmation. Never automate the real approval form. Adjustments require a new proposal and approval; viewing details does not.

# Provenance

This plugin adapts and improves the independent-answer, anonymized-review and chairman workflow from [HaroldZhong/ai-advisory-board](https://github.com/HaroldZhong/ai-advisory-board). The original application is the design baseline. Initial inspection used `e488ef2bff76064ccde4f7ef077b3f96a8854844` (v1.2.1); the 1.1.1 preservation review verified default-branch commit `b5d687820e88c10de25a9a2343d3cc478e497524` on 2026-10-23.

`runtime/harness.py` retains the original parser, average-rank/population-variance consensus functions, aggregation and base review/chairman templates from `backend/council.py`. A pinned source fixture supports parity tests. These derived portions remain under the original MIT license, copyright 2025 Andrej Karpathy (Original Author) and HaroldZhong (Enhancements); the complete notice is in `licenses/ai-advisory-board-MIT.txt`.

Explicit extensions are a shared task-specific rubric, strict ballot validation, shuffled persistent reviewer label maps, an isolated Codex participant, complete-stage barriers and approval/cost controls. Original rank mathematics is preserved; the rubric changes elicitation and does not claim identical empirical behavior or calibrated correctness probabilities. The upstream silent chairman retry and automatic return to single-model Chat are replaced by explicit approval and persistent board mode. The non-defensive research-writing policy generalizes an author-supplied writing-principles document and its existing project application; no study findings, clinical data or private project records are packaged. See `skills/board-with-codex/references/deliberation.md`.

The task-framing methods reflect customized grill-me and writing-for-agents skills and the reviewed [Matt Pocock skills collection](https://github.com/mattpocock/skills). The packaged instructions are a focused new integration, not an installation or replacement of that collection.

Technical references checked 2026-10-22/23:

- [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins) and [local MCP packaging](https://developers.openai.com/plugins/deploy/desktop-only).
- [Google Cloud native Gemini inference](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/4-argon) and [Claude models](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/partner-models/claude).
- [Google Cloud generative AI pricing](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing).
- [gcloud installation](https://docs.cloud.google.com/sdk/docs/install-sdk), [ADC login](https://docs.cloud.google.com/sdk/gcloud/reference/auth/application-default/login) and [service status checks](https://docs.cloud.google.com/service-usage/docs/reference/rest/v1/services/get).

The Google Cloud CLI is downloaded separately from Google, retains its own licenses/notices and is not bundled into this plugin's source.

Public and personal installation packages use the same implementation with different edition defaults. Packages contain no user credentials, account settings or meeting records. Preparing a public package does not publish it to a marketplace. The plugin-specific implementation is available under the MIT License in `LICENSE`. Upstream-derived portions retain their original MIT copyright and license notices in `licenses/ai-advisory-board-MIT.txt`. Provider services, trademarks and their terms remain independent.

Additional protocol and evaluation references: [Google MaaS](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/call-open-model-apis), [Claude Google-managed integration](https://platform.claude.com/docs/en/build-with-claude/claude-models-on-google-cloud/google-managed), [xAI OpenAPI](https://docs.x.ai/openapi.json), [xAI costs](https://docs.x.ai/developers/cost-tracking), [Artificial Analysis](https://artificialanalysis.ai/leaderboards/models), [Arena](https://arena.ai/leaderboard), [LiveBench](https://livebench.ai/), [SWE-bench](https://www.swebench.com/).

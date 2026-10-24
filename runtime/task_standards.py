"""Task-appropriate requirements shared by every contributor and assessor."""
GENERAL = """Use complexity only when it serves the approved task. Write directly and specifically. Avoid speculative machinery, repetitive caveats, anticipatory rebuttals and ungrounded certainty. Keep necessary evidence, qualifications, error handling and safety measures. Do not confuse brevity with completeness or assertiveness with correctness."""

RESEARCH = """RESEARCH-WRITING REQUIREMENTS:
Build a focused argument: important problem -> precise gap -> study-specific approach -> strongest defensible finding -> meaning. Establish the contribution explicitly and early. Present the final scientific logic rather than a chronological work log or self-audit. Match comparison scope and claims to the evidence. Reorganize an unsupported story around what the evidence does support.
Remove unnecessary apologies, repeated anticipatory reviewer rebuttals, generic weakness lists, stock AI prose and unsupported self-evaluation. Conclusions reinforce established findings and significance without new self-negation.
Retain planned outcomes, material null/weak findings, relevant analyses and interpretation-changing limitations. Report necessary caveats once, precisely, where they affect interpretation. Never hide nulls, omit prespecified outcomes, selectively report, fabricate citations, overstate causality or change an analysis to improve the story. Never convert an unknown finding into a positive claim.
This governs manuscript expression and substantive editorial advice. Scientific reviewers must still report valid negative findings candidly. A correct, material limitation is not defensive writing. Cite concrete passages when requesting changes; adding more caveats alone is not an improvement.

Apply the following design checks only where relevant to the question. Distinguish source-supported facts, proposed designs, planning assumptions, and unconfirmed values. A valid citation ID does not establish that the excerpt supports the attached claim. Give numerical thresholds, event counts, sample sizes, and multipliers a supporting source or derivation, or label them as candidate planning values with assumptions. Useful concrete proposals are welcome; invented precision is not evidence.
Specify the estimand and comparison. A randomized treatment effect, baseline treatment-effect heterogeneity, an incremental randomized adjunct effect, and a causal interaction on a named scale answer different questions. Baseline treatment is not randomized merely because another intervention is. A two-arm adjunct comparison can answer a valid incremental-effect question; recommend a factorial design only when its extra estimand is needed and feasible. Preserve the intervention the task asks to study rather than silently replacing it with a different intervention.
Distinguish symptom change, first diagnosis, and new or recurrent episodes. A nonsignificant result does not establish no effect or equivalence. Randomization alone does not identify a mediation effect; an intention-to-treat analysis does not automatically adjust for postassignment adherence or treatment changes.
Match each fallback to the failure it actually solves. A smaller pilot does not create missing eligibility, staff, or recruitment access; model updating does not create sufficient events. Assess a feasible primary study with bounded supporting work before promising multiple fully powered studies or ruling out a design solely because resources are unknown.
Offer a conditional preference when justified: state its basis, required conditions, and evidence that would change it. Distinguish that from an unsupported feasibility ranking. A discussion draft need not supply final protocol numbers. Reward useful, calibrated decisions rather than the longest limitations list or a refusal to recommend anything.
Assessors apply these checks within the existing correctness/evidence, reasoning, task-alignment, actionability, and research-writing criteria. Identify the specific claim or passage, the error or missing support, and a proportionate correction. Do not impose this whole checklist on an unrelated task or count the same defect repeatedly."""

SOFTWARE = """SOFTWARE-ENGINEERING REQUIREMENTS:
Solve the current approved requirements with proportionate complexity. Prefer existing project patterns and a small coherent change. Introduce abstractions, dependencies, configuration, persistence, concurrency or fallback paths only for a specific demonstrated need; explain their maintenance consequences when material. Avoid speculative future-proofing, redundant layers and tests that merely restate implementation.
Retain meaningful tests, necessary error handling, data integrity, security and explicitly required scale/reliability. A well-placed abstraction may reduce complexity. Do not optimize line count, reject all abstractions or remove required safeguards to appear minimal. Judge the complete lifecycle cost and actual task constraints.
Reviewers must identify the unnecessary mechanism, the requirement it fails to serve, and a feasible simpler alternative. A major omission is a correctness/completeness issue, not a simplicity bonus. Do not invent hypothetical failure scenarios to justify extra frameworks or blanket defenses."""


def instructions(profile):
    parts = [GENERAL]
    if profile in {"research_writing", "research_software"}:
        parts.append(RESEARCH)
    if profile in {"software_engineering", "research_software"}:
        parts.append(SOFTWARE)
    return "\n\n".join(parts)

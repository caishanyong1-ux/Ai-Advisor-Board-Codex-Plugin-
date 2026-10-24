"""Stage-scoped delivery requirements and the board's shared language policy."""
from .core import BoardError, text_field


LANGUAGE = "Use American English (en-US) for the board's internal discussion, analysis, review prose, score reasons, and chairman synthesis. Formal task deliverables use the approved deliverable language; clearly distinguish them from internal discussion. Preserve source quotations, identifiers, code, and evidence in their original form. The host presents user-facing reports, conversation, and functional interfaces in the current conversation language; that separate presentation preference does not change the board's working language."


def contract(raw, live):
    """Freeze member requirements separately from the chairman's deliverable."""
    proposal = {}
    for key in ("deliverable", "success_criteria"):
        name = "member_" + key
        value = raw.get(name)
        if value is None and not live:
            value = raw.get(key)
        proposal[key] = text_field(value, name, 12000)
    return {"version": "stage-delivery-v1", "language": "en-US",
            "deliverable_language": text_field(raw.get("deliverable_language", "en-US"), "deliverable_language", 240),
            "proposal": proposal,
            "review": {"assesses": "proposal", "format": "Narrative evaluations, SCORES_JSON, and FINAL RANKING"},
            "chairman": {key: raw[key] for key in ("deliverable", "success_criteria")},
            "user_interaction_language": "conversation preference (presentation only)"}


def instructions(plan, stage):
    if stage not in {"proposal", "review", "chairman"}:
        raise BoardError("Unknown delivery stage")
    frozen = plan.get("stage_contracts")
    if not frozen:
        # Reading historical meetings must not invent a new approved contract.
        return "Historical shared requirements (stage contracts were not recorded):\nDeliverable:\n%s\nSuccess criteria:\n%s" % (plan["deliverable"], plan["success_criteria"])
    if stage == "review":
        target = frozen["proposal"]
        scope = "Review only the member deliverable below. Chairman synthesis, downstream execution, and user-facing translation are separate stages, not omissions in a member answer. Apply the same content, language, and scoring requirements to every response, including the independent Codex response."
    elif stage == "proposal":
        target = frozen["proposal"]
        scope = "Every independent member, including Codex, completes this same deliverable. Member-specific focus adds perspective; it cannot change the shared language, required content, or review criteria. Chairman synthesis and downstream execution are separate stages."
    else:
        target = frozen["chairman"]
        scope = "Synthesize the members' work against the chairman deliverable. Evaluate member omissions only against their own approved stage contract. Preserve justified disagreements and distinguish the meeting conclusion from later user decisions or project closure."
    language = "Formal task deliverable language: %s. Review required artifact language against this contract, not the user's chat language. Keep working prose in American English." % frozen["deliverable_language"]
    return "%s STAGE CONTRACT:\n%s\n%s\n%s\nDeliverable:\n%s\nSuccess criteria:\n%s" % (stage.upper(), LANGUAGE, language, scope, target["deliverable"], target["success_criteria"])


def validate_evidence_delta(items):
    """Keep chairman-only evidence explicitly separate from frozen member evidence."""
    from datetime import date
    from .core import strict_keys
    if not isinstance(items, list) or len(items) > 30:
        raise BoardError("evidence_delta must contain at most 30 source records")
    result = []
    for item in items:
        strict_keys(item, {"source", "checked_at", "affected_claims", "finding", "member_visibility"}, "evidence delta")
        record = {key: text_field(item.get(key), key, 8000 if key in {"affected_claims", "finding"} else 2000) for key in ("source", "checked_at", "affected_claims", "finding")}
        try:
            date.fromisoformat(record["checked_at"])
        except ValueError:
            raise BoardError("Evidence checked_at must be a YYYY-MM-DD date") from None
        if item.get("member_visibility") != "chairman_only":
            raise BoardError("Post-meeting evidence is chairman_only; shared evidence belongs in a separately approved source snapshot")
        record["member_visibility"] = "chairman_only"
        result.append(record)
    return result

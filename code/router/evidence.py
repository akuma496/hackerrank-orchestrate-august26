"""Deterministic evidence retrieval: no embeddings, no LLM. Ranks
message_history.csv rows by lineage (same sender > same business > same
group > cross-user pattern match) x content similarity x event outcome,
returns the top MAX_EVIDENCE candidates. decide.py may only cite from this
pool -- validate.py enforces that."""

from dataclasses import dataclass

from . import config
from .textsim import jaccard

LINEAGE_WEIGHT = {"sender": 4, "business": 3, "group": 2, "cross_user_pattern": 1}

# Event-outcome weight: how informative this piece of evidence is, not a
# direction judgment -- a reported/dismissed history row is just as useful
# evidence for a mute call as a replied-fast row is for a notify call.
EVENT_WEIGHT = {
    "reported": 1.3,
    "muted_after": 1.25,
    "replied": 1.2,
    "dismissed": 1.1,
    "opened": 1.0,
    "none": 0.8,
}


@dataclass
class EvidenceItem:
    message_id: str
    lineage: str
    similarity: float
    event_summary: str
    score: float


def _event_summary(event: dict | None) -> tuple:
    """Returns (summary_string, event_kind_key)."""
    if not event:
        return "no recorded reaction", "none"
    if event.get("message_reported") == "1":
        return "user reported this sender before", "reported"
    if event.get("muted_after_message") == "1":
        return "user muted after a similar message", "muted_after"
    if event.get("message_replied") == "1":
        return "user replied quickly to a similar message", "replied"
    if event.get("notification_dismissed") == "1":
        return "user dismissed a similar message", "dismissed"
    if event.get("message_opened") == "1":
        return "user opened but did not act on a similar message", "opened"
    return "no recorded reaction", "none"


def _candidates(bundle, ctx) -> list:
    """Returns [(history_row, lineage)] deduped, keeping the strongest lineage per message_id."""
    pool: dict = {}

    def add(rows, lineage):
        for h in rows:
            if h["user_id"] != bundle.user_id:
                continue
            existing = pool.get(h["message_id"])
            if not existing or LINEAGE_WEIGHT[lineage] > LINEAGE_WEIGHT[existing[1]]:
                pool[h["message_id"]] = (h, lineage)

    if bundle.sender_user_id:
        add(ctx.message_history_by_sender.get(bundle.sender_user_id, []), "sender")
    if bundle.business_id:
        add(ctx.message_history_by_business.get(bundle.business_id, []), "business")
    if bundle.group_id:
        add(ctx.message_history_by_group.get(bundle.group_id, []), "group")

    if bundle.content["entities"]["otp_or_fee_ask"] or bundle.trust.get("domain_mismatch"):
        for h in ctx.message_history:
            if h["message_id"] in pool:
                continue
            if h["user_id"] == bundle.user_id:
                continue
            same_business = bundle.business_id and h.get("business_id") == bundle.business_id
            similar_text = jaccard(h["message_text"], bundle.message_text) >= 0.35
            if same_business or similar_text:
                pool[h["message_id"]] = (h, "cross_user_pattern")

    return list(pool.values())


def rank_evidence(bundle, ctx, transcript_text: str | None = None) -> list:
    text_for_similarity = transcript_text if transcript_text else bundle.message_text
    items = []
    for history_row, lineage in _candidates(bundle, ctx):
        similarity = jaccard(history_row["message_text"], text_for_similarity)
        event = ctx.message_events.get((bundle.user_id, history_row["message_id"]))
        summary, event_kind = _event_summary(event)
        score = LINEAGE_WEIGHT[lineage] * (1 + similarity) * EVENT_WEIGHT[event_kind]
        items.append(
            EvidenceItem(
                message_id=history_row["message_id"],
                lineage=lineage,
                similarity=similarity,
                event_summary=summary,
                score=round(score, 3),
            )
        )
    items.sort(key=lambda i: (-i.score, i.message_id))
    return items[: config.MAX_EVIDENCE]


def candidate_id_set(bundle, ctx) -> set:
    """Full candidate pool (not just the top MAX_EVIDENCE) -- used to validate
    that an LLM only cites IDs that are actually plausible evidence."""
    return {h["message_id"] for h, _ in _candidates(bundle, ctx)}


def evidence_ids_field(items: list) -> str:
    if not items:
        return "none"
    return ";".join(i.message_id for i in items)

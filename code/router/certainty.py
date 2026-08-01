"""Certainty engine: confidence is computed from signal-family convergence,
never self-reported by the LLM (see PLAN.md sec 5). Five families vote
notify/digest/mute/abstain with a strength; the spread across non-abstaining
votes determines certain/confident/conflict/uninformed."""

from . import config
from .rules import INJECTION_RE, PROMO_RE, TIME_CRITICAL_RE

_WEIGHT = {"strong": 2, "medium": 1, "weak": 0.5}


def trust_vote(bundle):
    t = bundle.trust
    if t.get("domain_mismatch") or t.get("business_verified") is False:
        return ("mute", "strong")
    if t.get("trusted_group") and t.get("sender_role") == "admin":
        return ("notify", "medium")
    if t.get("risky_group"):
        return ("mute", "weak")
    if t.get("business_verified") is True:
        return ("digest", "weak")
    return ("abstain", "weak")


def relationship_vote(bundle):
    r = bundle.relationship
    if r.get("promotions_opted_out"):
        return ("mute", "strong")
    if r.get("recipient_group_muted"):
        return ("mute", "medium")
    if r.get("has_replied_to_sender_before"):
        return ("notify", "medium")
    if (r.get("activity_count_180d") or 0) > 0:
        return ("digest", "weak")
    return ("abstain", "weak")


def behavior_vote(bundle):
    b = bundle.behavior
    if not b.get("sample_size"):
        return ("abstain", "weak")
    if (b.get("reported_count") or 0) > 0 or (b.get("muted_after_count") or 0) > 0:
        return ("mute", "strong")
    if (b.get("replied_rate") or 0) >= 0.5:
        return ("notify", "medium")
    if (b.get("dismissed_rate") or 0) >= 0.6:
        return ("mute", "medium")
    if (b.get("opened_rate") or 0) >= 0.5:
        return ("digest", "weak")
    return ("abstain", "weak")


def content_vote(bundle, transcript_text=None):
    text = transcript_text if transcript_text else bundle.message_text
    c = bundle.content
    if INJECTION_RE.search(text or ""):
        return ("mute", "strong")
    if c["entities"]["otp_or_fee_ask"]:
        return ("mute", "strong")
    if c["mentions_recipient"]:
        return ("notify", "strong")
    if TIME_CRITICAL_RE.search(text or ""):
        return ("notify", "medium")
    if PROMO_RE.search(text or ""):
        return ("digest", "weak")
    return ("abstain", "weak")


def compute_family_votes(bundle, transcript_text=None) -> dict:
    return {
        "trust": trust_vote(bundle),
        "relationship": relationship_vote(bundle),
        "behavior": behavior_vote(bundle),
        "content": content_vote(bundle, transcript_text),
    }


def compute_certainty(votes: dict) -> tuple:
    """votes: family -> (direction, strength), may include 'llm'. Returns
    (state, leaning_direction) where state in certain/confident/conflict/uninformed.

    The LLM's own vote never counts as corroboration for its own certainty --
    if all four deterministic families (trust/relationship/behavior/content)
    abstain, the state is 'uninformed' regardless of what the LLM says,
    because there is no independent signal backing its opinion."""
    deterministic_tally: dict = {}
    for family, (direction, strength) in votes.items():
        if family == "llm" or direction == "abstain":
            continue
        deterministic_tally[direction] = deterministic_tally.get(direction, 0) + _WEIGHT[strength]

    if not deterministic_tally:
        return "uninformed", votes.get("llm", (None, None))[0]

    tally = dict(deterministic_tally)
    if "llm" in votes:
        direction, strength = votes["llm"]
        if direction != "abstain":
            tally[direction] = tally.get(direction, 0) + _WEIGHT[strength]

    ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    top_dir, top_w = ranked[0]
    if len(ranked) == 1:
        return "certain", top_dir

    second_dir, second_w = ranked[1]
    if second_w == 0:
        return "certain", top_dir
    if top_w / second_w >= 2:
        return "confident", top_dir
    return "conflict", top_dir


def pick_confidence(state: str, *, hard_rule: bool = False, escalated: bool = False) -> float:
    lo, hi = config.CONFIDENCE_BANDS[state]
    if state == "certain":
        return hi if hard_rule else lo
    if state == "confident":
        return round((lo + hi) / 2, 2)
    if state == "conflict":
        return hi if escalated else lo
    return lo  # uninformed

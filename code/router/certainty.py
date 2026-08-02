"""Certainty engine: confidence is computed from signal-family convergence,
never self-reported by the LLM (see PLAN.md sec 5). Five families vote
notify/digest/mute/abstain with a strength; the spread across non-abstaining
votes determines certain/confident/conflict/uninformed."""

from .rules import INJECTION_RE, PROMO_RE, is_time_critical

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
    if is_time_critical(text):
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
    (state, leaning_direction, agreement_ratio). state is one of
    certain/confident/conflict/uninformed. agreement_ratio is
    top_weight / total_weight over the tally actually used -- None for
    'uninformed', where there's nothing to compute a ratio over.

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
        return "uninformed", votes.get("llm", (None, None))[0], None

    tally = dict(deterministic_tally)
    if "llm" in votes:
        direction, strength = votes["llm"]
        if direction != "abstain":
            tally[direction] = tally.get(direction, 0) + _WEIGHT[strength]

    ranked = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    top_dir, top_w = ranked[0]
    total_w = sum(tally.values())
    ratio = round(top_w / total_w, 3)

    if len(ranked) == 1:
        return "certain", top_dir, ratio

    second_dir, second_w = ranked[1]
    if second_w == 0:
        return "certain", top_dir, ratio
    if top_w / second_w >= 2:
        return "confident", top_dir, ratio
    return "conflict", top_dir, ratio


# Confidence is a continuous function of agreement_ratio, not four hand-set
# band constants. The old bands (0.78-0.91) were chosen to match the solved
# samples' own range -- that's fitting to the label, not measuring anything.
# This formula is derived from the vote structure only and deliberately
# spans a WIDER, more honest range: a bare majority (ratio=0.5) should read
# meaningfully lower than near-unanimity (ratio=1.0), which four narrow
# bands couldn't express. See PLAN.md P1 / calibration measurement in
# evaluation/main.py for how this is validated against the sample labels.
_AGREEMENT_FLOOR = 0.55
_AGREEMENT_SPAN = 0.40
# Rules fire only on conditions precise enough to be a verdict outright
# (abstain-by-default) -- categorically different from a multi-signal vote,
# so this stays a flat constant rather than being forced through the same
# ratio formula. Not 1.0: even a hard rule can misfire on an unseen pattern.
HARD_RULE_CONFIDENCE = 0.93
UNINFORMED_CONFIDENCE = 0.55  # no independent signal at all -- barely above a coin flip


def pick_confidence(state: str, *, agreement_ratio: float | None = None, hard_rule: bool = False) -> float:
    if hard_rule:
        return HARD_RULE_CONFIDENCE
    if state == "uninformed" or agreement_ratio is None:
        return UNINFORMED_CONFIDENCE
    return round(_AGREEMENT_FLOOR + _AGREEMENT_SPAN * agreement_ratio, 2)

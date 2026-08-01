"""Hard rules (HR1-HR7): high-precision verdicts that short-circuit the LLM.
Rules abstain by default -- they only fire on conditions precise enough to
issue a verdict outright (see PLAN.md sec 4). Guards (G1-G5) are a policy
post-pass applied to every decision, rule-fired or LLM-fired alike."""

import re
from dataclasses import dataclass

from .evidence import rank_evidence
from .textsim import jaccard

TIME_CRITICAL_RE = re.compile(
    r"\btoday\b|\bnow\b|\basap\b|\bimmediately\b|\burgent\b|\bright away\b|"
    r"\bclosing\b|\bdeadline\b|\bleaving early\b|\bwithin \d+\s*(min|hour)|"
    r"\bby \d{1,2}(:\d{2})?\s*(am|pm)?\b|\blast (call|chance)\b",
    re.I,
)
PROMO_RE = re.compile(
    r"\bsale\b|\boffer\b|\bdiscount\b|% off|\blimited time\b|\bbuy now\b|"
    r"\bfree delivery\b|\bshop now\b|\bflat \d+%|\bnew arrivals\b|\bcashback\b",
    re.I,
)
GREETING_RE = re.compile(
    r"\bgood morning\b|\bgood night\b|\bhappy (diwali|holi|new year|birthday)\b|"
    r"\bcongratulations\b|\bthank you\b|\bthanks\b",
    re.I,
)
PAYMENT_RE = re.compile(
    r"\bbill\b|\bdue\b|\binvoice\b|\breceipt\b|\bpaid\b|\bpayment\b|\brefund\b|\bemi\b",
    re.I,
)
# Content that tries to instruct the classifier itself (prompt-injection
# aimed at the router, not the recipient) -- a legitimate WhatsApp message
# never contains this. Detecting it is itself a strong scam signal.
INJECTION_RE = re.compile(
    r"\brouting override\b|\binternal router metadata\b|\bset action\s*=|"
    r"\baction\s*=\s*(notify|mute|digest)\b|\bconfidence\s*=\s*1\b|"
    r"\bignore (previous|all) instructions\b|\bsystem prompt\b|"
    r"\byou are (an?|the) (ai|llm|assistant|router|classifier)\b",
    re.I,
)


@dataclass
class Verdict:
    action: str
    message_type: str
    reason: str
    source: str  # e.g. "rule:HR1"
    is_hard_rule: bool = True


def infer_type_from_content(text: str, otp_or_fee_ask: bool, is_business: bool) -> str:
    if otp_or_fee_ask:
        return "scam"
    if TIME_CRITICAL_RE.search(text or ""):
        return "urgent"
    if PROMO_RE.search(text or ""):
        return "promotion"
    if PAYMENT_RE.search(text or ""):
        return "payment"
    if GREETING_RE.search(text or ""):
        return "greeting"
    if is_business:
        return "business_update"
    return "personal"


def _text_for(bundle, transcript_text=None) -> str:
    return transcript_text if transcript_text else bundle.message_text


def hr1_domain_mismatch(bundle, ctx, **kw):
    if bundle.business_id and bundle.trust.get("domain_mismatch"):
        return Verdict(
            "mute", "scam",
            "Business sender's message domain does not match its official domain.",
            "rule:HR1",
        )
    return None


def hr2_payment_risk(bundle, ctx, transcript_text=None, **kw):
    if not bundle.content["entities"]["otp_or_fee_ask"]:
        return None
    unverified = bundle.trust.get("business_verified") is False
    young_domain = (bundle.trust.get("domain_used_age_days") or 9999) < 30
    no_relationship = (bundle.relationship.get("activity_count_180d") or 0) == 0
    if unverified or young_domain or no_relationship:
        return Verdict(
            "mute", "scam",
            "Message asks for payment/OTP from a sender with no verified relationship to the user.",
            "rule:HR2",
        )
    return None


def hr3_promo_opted_out(bundle, ctx, transcript_text=None, **kw):
    text = _text_for(bundle, transcript_text)
    if bundle.relationship.get("promotions_opted_out") and PROMO_RE.search(text or ""):
        return Verdict(
            "mute", "promotion",
            "User opted out of promotions from this business, and this is a promotional message.",
            "rule:HR3",
        )
    return None


def hr4_previously_reported(bundle, ctx, transcript_text=None, **kw):
    if (bundle.behavior.get("reported_count") or 0) > 0:
        text = _text_for(bundle, transcript_text)
        mtype = infer_type_from_content(
            text, bundle.content["entities"]["otp_or_fee_ask"], bool(bundle.business_id)
        )
        if mtype not in ("scam", "spam"):
            mtype = "spam"
        return Verdict(
            "mute", mtype,
            "User previously reported messages from this sender.",
            "rule:HR4",
        )
    return None


def hr5_direct_mention(bundle, ctx, transcript_text=None, **kw):
    if not (bundle.content["mentions_recipient"] and bundle.conversation_type == "group"):
        return None
    if (bundle.behavior.get("reported_count") or 0) > 0 or bundle.trust.get("risky_group"):
        return None
    text = _text_for(bundle, transcript_text)
    mtype = infer_type_from_content(text, False, False)
    if mtype in ("promotion", "greeting", "business_update"):
        mtype = "personal"
    return Verdict(
        "notify", mtype,
        "Message directly mentions this user in a group conversation.",
        "rule:HR5",
    )


def hr6_trusted_admin_time_critical(bundle, ctx, transcript_text=None, **kw):
    text = _text_for(bundle, transcript_text)
    if bundle.trust.get("trusted_group") and bundle.trust.get("sender_role") == "admin" and TIME_CRITICAL_RE.search(text or ""):
        mtype = "urgent" if re.search(r"\bnow\b|\basap\b|\burgent\b|\bimmediately\b", text or "", re.I) else "event"
        return Verdict(
            "notify", mtype,
            "A trusted group admin sent a time-sensitive operational update.",
            "rule:HR6",
        )
    return None


def hr7_ignored_duplicate(bundle, ctx, transcript_text=None, **kw):
    if bundle.conversation_type == "personal":
        return None
    text = _text_for(bundle, transcript_text)
    dismissed_matches = 0
    for item in rank_evidence(bundle, ctx, transcript_text=text):
        if item.similarity >= 0.35 and item.event_summary.startswith(("user dismissed", "user muted")):
            dismissed_matches += 1
    if dismissed_matches >= 2:
        mtype = "promotion" if PROMO_RE.search(text or "") else "spam"
        return Verdict(
            "mute", mtype,
            "Near-duplicate of multiple similar messages the user has previously dismissed.",
            "rule:HR7",
        )
    return None


def hr8_router_injection(bundle, ctx, transcript_text=None, **kw):
    text = _text_for(bundle, transcript_text)
    if INJECTION_RE.search(text or ""):
        return Verdict(
            "mute", "scam",
            "Message content attempts to instruct the classifier directly (prompt-injection pattern) -- treated as a scam/manipulation signal.",
            "rule:HR8",
        )
    return None


HARD_RULES = [
    hr8_router_injection,  # checked first: most specific + most severe signal
    hr1_domain_mismatch,
    hr2_payment_risk,
    hr3_promo_opted_out,
    hr4_previously_reported,
    hr5_direct_mention,
    hr6_trusted_admin_time_critical,
    hr7_ignored_duplicate,
]


def apply_hard_rules(bundle, ctx, transcript_text=None) -> Verdict | None:
    for rule in HARD_RULES:
        verdict = rule(bundle, ctx, transcript_text=transcript_text)
        if verdict:
            return verdict
    return None


# --- Guards (G1-G5): policy post-pass applied to every decision ---------

def apply_guards(action: str, message_type: str, bundle, source: str) -> tuple:
    """Returns (action, message_type, notes) after G1-G5. `notes` records any
    guard that changed the outcome, for the reason/audit trail."""
    notes = []

    # G1: personal-sender guard -- no rule-mute on content alone.
    if (
        bundle.conversation_type == "personal"
        and action == "mute"
        and source.startswith("rule:")
        and (bundle.behavior.get("reported_count") or 0) == 0
    ):
        action = "digest"
        notes.append("G1: personal sender, no behavioral evidence to support mute -> floor digest")

    # G2: DND demotion -- unless urgent or personal.
    if (
        action == "notify"
        and bundle.content.get("in_dnd_window")
        and message_type not in ("urgent",)
        and bundle.conversation_type != "personal"
    ):
        action = "digest"
        notes.append("G2: inside recipient's DND window, demoted from notify")

    return action, message_type, notes

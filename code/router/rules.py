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
# "Nothing urgent" / "not urgent" / "no rush" explicitly disclaims urgency --
# a bare regex match on "urgent" without this check reads the negation as
# the opposite of what it says.
NOT_TIME_CRITICAL_RE = re.compile(
    r"\bnothing urgent\b|\bnot urgent\b|\bno rush\b|\bno hurry\b|"
    r"\bwhenever (you|it'?s)\b|\bno need to rush\b",
    re.I,
)


def is_time_critical(text: str) -> bool:
    if not text:
        return False
    if NOT_TIME_CRITICAL_RE.search(text):
        return False
    return bool(TIME_CRITICAL_RE.search(text))
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
    r"\brouting override\b|\binternal router metadata\b|\bsystem note\b|"
    r"\bset action\s*=|\baction\s*=\s*(notify|mute|digest)\b|\bconfidence\s*=\s*1\b|"
    r"\bignore (previous|all) instructions\b|\bsystem prompt\b|"
    r"\balways (mark|label|classify|flag) this as\b|\bmark this (message |conversation )?as (notify|mute|digest|urgent|important)\b|"
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
    # Detects unambiguous safety risk (spoofed domain, OTP/payment-code
    # scam, injection attack) rather than a soft content heuristic. Carried
    # on the verdict itself so G1 doesn't depend on a separately-maintained
    # source-string allowlist staying in sync with the rule set -- a rule
    # that forgets to set this is exempt from nothing, which fails safe
    # (falls back to G1's protection) rather than failing open.
    safety_critical: bool = False


def infer_type_from_content(text: str, otp_or_fee_ask: bool, is_business: bool) -> str:
    if otp_or_fee_ask:
        return "scam"
    if is_time_critical(text):
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


def _describe_otp_fee_signal(text: str) -> str:
    """otp_or_fee_ask covers several distinct scam mechanics -- name the one
    that actually matched instead of a generic 'OTP/payment code' phrase
    that's wrong for e.g. a QR-payment or verify-via-link pattern."""
    text = text or ""
    if re.search(r"\botp\b|\b\d{1,2}[\s-]?digit (code|otp|pin)\b|\b(login|verification|security|access)\s+code\b", text, re.I):
        return "an OTP/verification code"
    if re.search(r"\breattempt fee\b|\bconvenience fee\b|\bpay.{0,15}(fee|charge)\b", text, re.I):
        return "a fee payment"
    if re.search(r"\bbank (details?|account details?)\b|\bcard details?\b", text, re.I):
        return "bank/card details"
    if re.search(r"\bscan (this|the) qr\b|\bqr code\b", text, re.I):
        return "a QR-code payment"
    if re.search(r"\bverify (through|via) this link\b", text, re.I):
        return "verification via a link"
    if re.search(r"\baccount (will be |may be )?(blocked|locked|restricted|suspended)\b|\bfailed login attempts?\b", text, re.I):
        return "action under an account-lock threat"
    if re.search(r"\brelease.{0,10}package\b", text, re.I):
        return "a package-release payment"
    return "a sensitive code or detail"


# A domain mismatch alone doesn't distinguish a spoofed identity from a
# legitimate business using a campaign/link-shortener domain (common WhatsApp
# marketing practice). A long-established, verified, low-complaint business
# gets the benefit of the doubt; a new or already-flagged one does not.
_LEGIT_MIN_ACCOUNT_AGE_DAYS = 180
_LEGIT_MIN_DOMAIN_AGE_DAYS = 90
_LEGIT_MAX_REPORTS_30D = 15


def _domain_mismatch_is_legit_pattern(trust: dict) -> bool:
    return (
        trust.get("business_verified") is True
        and (trust.get("account_age_days") or 0) >= _LEGIT_MIN_ACCOUNT_AGE_DAYS
        and (trust.get("domain_used_age_days") or 0) >= _LEGIT_MIN_DOMAIN_AGE_DAYS
        and (trust.get("business_reports_30d") or 0) < _LEGIT_MAX_REPORTS_30D
    )


def hr1_domain_mismatch(bundle, ctx, transcript_text=None, **kw):
    if not (bundle.business_id and bundle.trust.get("domain_mismatch")):
        return None
    if _domain_mismatch_is_legit_pattern(bundle.trust):
        return None
    reason = "Business sender's message domain does not match its official domain."
    if bundle.content["entities"]["otp_or_fee_ask"]:
        text = _text_for(bundle, transcript_text)
        reason += f" The message also asks for {_describe_otp_fee_signal(text)}, reinforcing the scam signal."
    return Verdict("mute", "scam", reason, "rule:HR1", safety_critical=True)


def hr2_payment_risk(bundle, ctx, transcript_text=None, **kw):
    if not bundle.content["entities"]["otp_or_fee_ask"]:
        return None
    text = _text_for(bundle, transcript_text)
    signal = _describe_otp_fee_signal(text)
    if bundle.business_id:
        unverified = bundle.trust.get("business_verified") is False
        young_domain = (bundle.trust.get("domain_used_age_days") or 9999) < 30
        no_relationship = (bundle.relationship.get("activity_count_180d") or 0) == 0
        if not (unverified or young_domain or no_relationship):
            return None
        reason = f"Message asks for {signal} from a business sender with no verified relationship to the user."
    else:
        # Not a business relationship to check -- these requests are muted
        # on content alone regardless of group/personal context, since
        # account compromise (a hacked family/group member's account asking
        # for this) is a common vector this can't distinguish from.
        reason = (
            f"Message asks for {signal} -- muted as a precaution regardless of "
            "sender relationship, since this exact request pattern is a common account-compromise vector."
        )
    return Verdict("mute", "scam", reason, "rule:HR2", safety_critical=True)


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
    # A @mention is someone addressing this user directly -- almost always
    # personal coordination. Only escalate away from 'personal' when the
    # content is genuinely time-critical; a stray "refund"/"bill" word in an
    # otherwise conversational mention doesn't make it a payment message.
    mtype = "urgent" if is_time_critical(text) else "personal"
    return Verdict(
        "notify", mtype,
        "Message directly mentions this user in a group conversation.",
        "rule:HR5",
    )


def hr6_trusted_admin_time_critical(bundle, ctx, transcript_text=None, **kw):
    text = _text_for(bundle, transcript_text)
    if bundle.trust.get("trusted_group") and bundle.trust.get("sender_role") == "admin" and is_time_critical(text):
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
        # Marketplace-group listings are peer-to-peer sale posts even
        # without classic promo keywords ("sale", "% off") -- the group
        # context itself signals promotion over generic spam.
        if PROMO_RE.search(text or "") or bundle.trust.get("group_type") == "marketplace":
            mtype = "promotion"
        else:
            mtype = "spam"
        return Verdict(
            "mute", mtype,
            "Near-duplicate of multiple similar messages the user has previously dismissed.",
            "rule:HR7",
        )
    return None


def hr8_router_injection(bundle, ctx, transcript_text=None, **kw):
    text = _text_for(bundle, transcript_text)
    if INJECTION_RE.search(text or ""):
        reason = "Message content attempts to instruct the classifier directly (prompt-injection pattern)."
        if bundle.content["entities"]["otp_or_fee_ask"]:
            signal = _describe_otp_fee_signal(text)
            reason += f" It also asks for {signal}, the underlying scam the injection was trying to disguise."
        return Verdict("mute", "scam", reason, "rule:HR8", safety_critical=True)
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


def apply_guards(action: str, message_type: str, bundle, source: str, safety_critical: bool = False) -> tuple:
    """Returns (action, message_type, notes) after G1-G5. `notes` records any
    guard that changed the outcome, for the reason/audit trail.

    `safety_critical` comes from the firing Verdict (see Verdict.safety_critical)
    for rule-sourced calls, and is False for LLM-sourced calls -- a rule that
    doesn't explicitly mark itself safety_critical is NOT exempt from G1,
    which fails safe rather than requiring a separately-maintained allowlist
    to stay in sync with the rule set."""
    notes = []

    # G1: personal-sender guard -- no rule-mute on ambiguous content alone.
    # Rules detecting unambiguous safety risk (safety_critical=True: spoofed
    # domain, OTP/payment scam, injection attack) are exempt -- the problem
    # statement is explicit that clear scam/safety risk mutes regardless of
    # the user's usual engagement. G1's "protect ambiguous personal content
    # from over-muting" intent does not apply to those.
    if (
        bundle.conversation_type == "personal"
        and action == "mute"
        and source.startswith("rule:")
        and not safety_critical
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

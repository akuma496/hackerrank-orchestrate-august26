"""Deterministic feature extraction per message: the five signal families
(trust, relationship, behavior, content) minus the LLM-read family, which is
produced later by llm.py. Every function here is pure and cache-free -- it
only reads from the in-memory Context built by loaders.py."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import config

_URL_RE = re.compile(r"https?://\S+|www\.\S+|\b[a-z0-9.-]+\.(?:in|com|net|org|co)\b/\S*", re.I)
_AMOUNT_RE = re.compile(r"(?:rs\.?|inr|₹)\s?\d[\d,]*(?:\.\d+)?", re.I)
_OTP_FEE_RE = re.compile(
    r"\botp\b|\breattempt fee\b|\bconvenience fee\b|\bpay.{0,15}(fee|charge)\b|"
    r"\brelease.{0,10}package\b|\b\d{1,2}[\s-]?digit (code|otp|pin)\b|"
    r"\b(login|verification|security|access)\s+code\b",
    re.I,
)
# A mention of OTP/fee/code language inside an explicit denial ("we never
# ask for your OTP") is a safety notice, not a request -- the opposite
# signal. Without this, a business's own anti-scam warning gets misread as
# the scam it's warning about.
_OTP_NEGATION_RE = re.compile(
    r"\b(never|don'?t|do not|won'?t|will not|should not|shouldn'?t)\s+"
    r"(ask|request|require|need)\w*\b[^.]{0,50}\b(otp|payment|fee|code|pin|password)",
    re.I,
)
_MENTION_RE = re.compile(r"@(u_\d+)")


def _has_otp_or_fee_ask(text: str) -> bool:
    if not text:
        return False
    if _OTP_NEGATION_RE.search(text):
        return False
    return bool(_OTP_FEE_RE.search(text))


def extract_entities(text: str) -> dict:
    """Deterministic entity extraction shared by message_text and media
    transcripts. Never receives user/recipient context -- content-only."""
    text = text or ""
    return {
        "urls": _URL_RE.findall(text),
        "amounts": _AMOUNT_RE.findall(text),
        "otp_or_fee_ask": _has_otp_or_fee_ask(text),
        "mentions": _MENTION_RE.findall(text),
    }


def _parse_dnd_window(window: str):
    if not window or "-" not in window:
        return None
    start_s, end_s = window.split("-", 1)
    try:
        start = datetime.strptime(start_s.strip(), "%H:%M").time()
        end = datetime.strptime(end_s.strip(), "%H:%M").time()
    except ValueError:
        return None
    return start, end


def in_dnd_window(created_at: str, dnd_window: str) -> bool:
    """True if created_at's local time-of-day falls inside dnd_window,
    handling windows that cross midnight (e.g. 22:00-07:00)."""
    parsed = _parse_dnd_window(dnd_window)
    if not parsed:
        return False
    start, end = parsed
    try:
        ts = datetime.strptime(created_at, "%Y-%m-%d %H:%M").time()
    except ValueError:
        return False
    if start <= end:
        return start <= ts <= end
    return ts >= start or ts <= end


@dataclass
class FeatureBundle:
    message_id: str
    user_id: str
    sender_user_id: str
    conversation_type: str
    group_id: str
    business_id: str
    created_at: str
    message_text: str
    media_type: str
    media_id: str
    forwarded_count: int

    trust: dict = field(default_factory=dict)
    relationship: dict = field(default_factory=dict)
    behavior: dict = field(default_factory=dict)
    content: dict = field(default_factory=dict)


def _trust_features(msg: dict, ctx) -> dict:
    trust = {
        "business_verified": None,
        "domain_mismatch": None,
        "account_age_days": None,
        "domain_used_age_days": None,
        "business_reports_30d": None,
        "group_type": None,
        "risky_group": False,
        "trusted_group": False,
        "sender_role": None,
    }
    if msg["business_id"]:
        biz = ctx.business_accounts.get(msg["business_id"])
        if biz:
            trust["business_verified"] = biz["verified"] == "1"
            # Requires a real official_domain to compare against -- an empty
            # official_domain (missing data, not a spoofing signal) must not
            # count as a mismatch.
            trust["domain_mismatch"] = (
                bool(biz["official_domain"])
                and bool(biz["domain_used_by_sender"])
                and biz["official_domain"] != biz["domain_used_by_sender"]
            )
            trust["account_age_days"] = int(biz["account_age_days"] or 0)
            trust["domain_used_age_days"] = int(biz["domain_used_by_sender_age_days"] or 0)
            trust["business_reports_30d"] = int(biz["user_reports_30d"] or 0)
    if msg["group_id"]:
        group = ctx.groups.get(msg["group_id"])
        if group:
            trust["group_type"] = group["group_type"]
            trust["risky_group"] = group["group_type"] in config.RISKY_GROUP_TYPES
            trust["trusted_group"] = group["group_type"] in config.TRUSTED_GROUP_TYPES
        member = ctx.group_members.get((msg["group_id"], msg["sender_user_id"]))
        if member:
            trust["sender_role"] = member["role"]
    return trust


def _relationship_features(msg: dict, ctx) -> dict:
    rel = {
        "allows_promotions": None,
        "promotions_opted_out": None,
        "activity_count_180d": None,
        "business_replies_30d": None,
        "recipient_group_role": None,
        "recipient_group_muted": None,
        "recipient_group_dismissed_30d": None,
        "has_replied_to_sender_before": None,
    }
    if msg["business_id"]:
        ubh = ctx.user_business_history.get((msg["user_id"], msg["business_id"]))
        if ubh:
            rel["allows_promotions"] = ubh["allows_promotions"] == "1"
            rel["promotions_opted_out"] = bool(ubh["promotions_opted_out_at"])
            rel["activity_count_180d"] = int(ubh["activity_count_180d"] or 0)
            rel["business_replies_30d"] = int(ubh["messages_replied_30d"] or 0)
    if msg["group_id"]:
        member = ctx.group_members.get((msg["group_id"], msg["user_id"]))
        if member:
            rel["recipient_group_role"] = member["role"]
            rel["recipient_group_muted"] = member["group_muted_by_user"] == "1"
            rel["recipient_group_dismissed_30d"] = int(
                member["notifications_dismissed_30d"] or 0
            )
    if msg["sender_user_id"]:
        past_from_sender = [
            h
            for h in ctx.message_history_by_sender.get(msg["sender_user_id"], [])
            if h["user_id"] == msg["user_id"]
        ]
        replied = any(
            ctx.message_events.get((msg["user_id"], h["message_id"]), {}).get(
                "message_replied"
            )
            == "1"
            for h in past_from_sender
        )
        rel["has_replied_to_sender_before"] = replied if past_from_sender else None
    return rel


def _behavior_features(msg: dict, ctx) -> dict:
    """Aggregate the recipient's past reaction pattern to messages sharing
    lineage with this one (same sender, or same business)."""
    candidates = []
    if msg["sender_user_id"]:
        candidates += [
            h
            for h in ctx.message_history_by_sender.get(msg["sender_user_id"], [])
            if h["user_id"] == msg["user_id"]
        ]
    if msg["business_id"]:
        candidates += [
            h
            for h in ctx.message_history_by_business.get(msg["business_id"], [])
            if h["user_id"] == msg["user_id"]
        ]
    seen_ids = set()
    events = []
    for h in candidates:
        if h["message_id"] in seen_ids:
            continue
        seen_ids.add(h["message_id"])
        ev = ctx.message_events.get((msg["user_id"], h["message_id"]))
        if ev:
            events.append(ev)

    n = len(events)
    if n == 0:
        return {
            "sample_size": 0,
            "opened_rate": None,
            "replied_rate": None,
            "dismissed_rate": None,
            "reported_count": None,
            "muted_after_count": None,
        }
    opened = sum(1 for e in events if e["message_opened"] == "1")
    replied = sum(1 for e in events if e["message_replied"] == "1")
    dismissed = sum(1 for e in events if e["notification_dismissed"] == "1")
    reported = sum(1 for e in events if e["message_reported"] == "1")
    muted_after = sum(1 for e in events if e["muted_after_message"] == "1")
    return {
        "sample_size": n,
        "opened_rate": round(opened / n, 2),
        "replied_rate": round(replied / n, 2),
        "dismissed_rate": round(dismissed / n, 2),
        "reported_count": reported,
        "muted_after_count": muted_after,
    }


def _content_features(msg: dict, ctx, transcript_entities: dict | None) -> dict:
    text = msg["message_text"]
    entities = extract_entities(text)
    if transcript_entities:
        entities = {
            "urls": entities["urls"] + transcript_entities.get("urls", []),
            "amounts": entities["amounts"] + transcript_entities.get("amounts", []),
            "otp_or_fee_ask": entities["otp_or_fee_ask"]
            or transcript_entities.get("otp_or_fee_ask", False),
            "mentions": entities["mentions"] + transcript_entities.get("mentions", []),
        }
    mentions_recipient = msg["user_id"] in entities["mentions"]
    user = ctx.users.get(msg["user_id"], {})
    return {
        "entities": entities,
        "mentions_recipient": mentions_recipient,
        "forwarded_count": int(msg["forwarded_count"] or 0),
        "in_dnd_window": in_dnd_window(msg["created_at"], user.get("do_not_disturb_window", "")),
        "text_length": len(text),
    }


def build_feature_bundle(msg: dict, ctx, transcript_entities: dict | None = None) -> FeatureBundle:
    return FeatureBundle(
        message_id=msg["message_id"],
        user_id=msg["user_id"],
        sender_user_id=msg["sender_user_id"],
        conversation_type=msg["conversation_type"],
        group_id=msg["group_id"],
        business_id=msg["business_id"],
        created_at=msg["created_at"],
        message_text=msg["message_text"],
        media_type=msg["media_type"],
        media_id=msg["media_id"],
        forwarded_count=int(msg["forwarded_count"] or 0),
        trust=_trust_features(msg, ctx),
        relationship=_relationship_features(msg, ctx),
        behavior=_behavior_features(msg, ctx),
        content=_content_features(msg, ctx, transcript_entities),
    )

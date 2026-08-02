"""Synthetic fixture builders for the rules/guards test suite. Deliberately
NOT built from dataset/ rows -- these must stay self-documenting and must
not silently break (or silently keep passing for the wrong reason) if the
dataset changes. See TESTING_PLAN.md."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router.features import FeatureBundle  # noqa: E402


def make_bundle(
    *,
    message_id="msg_test",
    user_id="u_test",
    sender_user_id="u_sender",
    conversation_type="personal",
    group_id="",
    business_id="",
    created_at="2026-07-30 12:00",
    message_text="hello",
    media_type="",
    media_id="",
    forwarded_count=0,
    trust=None,
    relationship=None,
    behavior=None,
    content=None,
) -> FeatureBundle:
    """Every field defaults to an inert/abstaining value -- a test only
    needs to override the fields its case actually cares about."""
    default_trust = {
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
    default_relationship = {
        "allows_promotions": None,
        "promotions_opted_out": None,
        "activity_count_180d": None,
        "business_replies_30d": None,
        "recipient_group_role": None,
        "recipient_group_muted": None,
        "recipient_group_dismissed_30d": None,
        "has_replied_to_sender_before": None,
    }
    default_behavior = {
        "sample_size": 0,
        "opened_rate": None,
        "replied_rate": None,
        "dismissed_rate": None,
        "reported_count": None,
        "muted_after_count": None,
    }
    default_content = {
        "entities": {"urls": [], "amounts": [], "otp_or_fee_ask": False, "mentions": []},
        "mentions_recipient": False,
        "forwarded_count": forwarded_count,
        "in_dnd_window": False,
        "text_length": len(message_text or ""),
    }

    if trust:
        default_trust.update(trust)
    if relationship:
        default_relationship.update(relationship)
    if behavior:
        default_behavior.update(behavior)
    if content:
        if "entities" in content:
            default_content["entities"].update(content.pop("entities"))
        default_content.update(content)

    return FeatureBundle(
        message_id=message_id,
        user_id=user_id,
        sender_user_id=sender_user_id,
        conversation_type=conversation_type,
        group_id=group_id,
        business_id=business_id,
        created_at=created_at,
        message_text=message_text,
        media_type=media_type,
        media_id=media_id,
        forwarded_count=forwarded_count,
        trust=default_trust,
        relationship=default_relationship,
        behavior=default_behavior,
        content=default_content,
    )


class FakeCtx:
    """Minimal stand-in for router.loaders.Context -- only what HR7's
    evidence lookup and rank_evidence() actually touch."""

    def __init__(self, history_rows=None, events=None):
        history_rows = history_rows or []
        self.message_history = history_rows
        self.message_history_by_id = {r["message_id"]: r for r in history_rows}
        self.message_history_by_sender = _group_by(history_rows, "sender_user_id")
        self.message_history_by_business = _group_by(history_rows, "business_id")
        self.message_history_by_group = _group_by(history_rows, "group_id")
        self.message_events = events or {}


def _group_by(rows, key):
    out = {}
    for row in rows:
        val = row.get(key)
        if not val:
            continue
        out.setdefault(val, []).append(row)
    return out


def make_history_row(message_id, user_id, message_text, **kw):
    row = {
        "message_id": message_id,
        "user_id": user_id,
        "conversation_type": kw.get("conversation_type", "group"),
        "group_id": kw.get("group_id", ""),
        "business_id": kw.get("business_id", ""),
        "sender_user_id": kw.get("sender_user_id", ""),
        "created_at": kw.get("created_at", "2026-07-01 10:00"),
        "message_text": message_text,
        "media_type": "",
        "media_id": "",
        "forwarded_count": "0",
    }
    return row


def make_event(user_id, message_id, *, opened="0", replied="0", dismissed="0", muted_after="0", reported="0"):
    return {
        "user_id": user_id,
        "message_id": message_id,
        "message_opened": opened,
        "message_replied": replied,
        "reaction_time_minutes": "",
        "notification_dismissed": dismissed,
        "muted_after_message": muted_after,
        "message_reported": reported,
    }

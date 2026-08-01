"""Load dataset/*.csv into indexed lookups. Pandas is used only here, for
robust CSV parsing (multiline quoted text, UTF-8) -- everything downstream
consumes plain dicts/lists, never a DataFrame."""

from dataclasses import dataclass, field

import pandas as pd

from . import config


def _read_csv(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    return df.to_dict(orient="records")


def _index_by(rows, key):
    return {row[key]: row for row in rows}


def _group_by(rows, key):
    out: dict = {}
    for row in rows:
        out.setdefault(row[key], []).append(row)
    return out


@dataclass
class Context:
    messages: list
    sample_messages: list
    users: dict
    groups: dict
    group_members: dict  # (group_id, user_id) -> row
    group_members_by_group: dict  # group_id -> [rows]
    business_accounts: dict
    user_business_history: dict  # (user_id, business_id) -> row
    message_history: list
    message_history_by_id: dict
    message_history_by_sender: dict
    message_history_by_business: dict
    message_history_by_group: dict
    message_history_by_user: dict
    message_events: dict  # (user_id, message_id) -> row
    images: dict
    voice_notes: dict
    daily_notification_summary: dict  # user_id -> [rows]
    integrity_warnings: list = field(default_factory=list)


def load_context() -> Context:
    messages = _read_csv(config.MESSAGES_CSV)
    sample_messages = _read_csv(config.SAMPLE_MESSAGES_CSV)
    users_rows = _read_csv(config.USERS_CSV)
    groups_rows = _read_csv(config.GROUPS_CSV)
    group_members_rows = _read_csv(config.GROUP_MEMBERS_CSV)
    business_rows = _read_csv(config.BUSINESS_ACCOUNTS_CSV)
    ubh_rows = _read_csv(config.USER_BUSINESS_HISTORY_CSV)
    history_rows = _read_csv(config.MESSAGE_HISTORY_CSV)
    events_rows = _read_csv(config.MESSAGE_EVENTS_CSV)
    images_rows = _read_csv(config.IMAGES_CSV)
    voice_rows = _read_csv(config.VOICE_NOTES_CSV)
    daily_rows = _read_csv(config.DAILY_NOTIFICATION_SUMMARY_CSV)

    ctx = Context(
        messages=messages,
        sample_messages=sample_messages,
        users=_index_by(users_rows, "user_id"),
        groups=_index_by(groups_rows, "group_id"),
        group_members={(r["group_id"], r["user_id"]): r for r in group_members_rows},
        group_members_by_group=_group_by(group_members_rows, "group_id"),
        business_accounts=_index_by(business_rows, "business_id"),
        user_business_history={(r["user_id"], r["business_id"]): r for r in ubh_rows},
        message_history=history_rows,
        message_history_by_id=_index_by(history_rows, "message_id"),
        message_history_by_sender=_group_by(
            [r for r in history_rows if r["sender_user_id"]], "sender_user_id"
        ),
        message_history_by_business=_group_by(
            [r for r in history_rows if r["business_id"]], "business_id"
        ),
        message_history_by_group=_group_by(
            [r for r in history_rows if r["group_id"]], "group_id"
        ),
        message_history_by_user=_group_by(history_rows, "user_id"),
        message_events={(r["user_id"], r["message_id"]): r for r in events_rows},
        images=_index_by(images_rows, "image_id"),
        voice_notes=_index_by(voice_rows, "voice_note_id"),
        daily_notification_summary=_group_by(daily_rows, "user_id"),
    )
    ctx.integrity_warnings = check_integrity(ctx)
    return ctx


def check_integrity(ctx: Context) -> list:
    """Non-fatal referential-integrity checks. Returns a list of warning strings."""
    warnings = []

    msg_ids = [m["message_id"] for m in ctx.messages]
    if len(msg_ids) != len(set(msg_ids)):
        warnings.append("duplicate message_id values in messages.csv")

    recipients = {m["user_id"] for m in ctx.messages}
    missing_recipients = recipients - set(ctx.users)
    if missing_recipients:
        warnings.append(f"recipients missing from users.csv: {missing_recipients}")

    senders = {m["sender_user_id"] for m in ctx.messages if m["sender_user_id"]}
    missing_senders = senders - set(ctx.users)
    if missing_senders:
        warnings.append(f"senders missing from users.csv: {missing_senders}")

    groups_referenced = {m["group_id"] for m in ctx.messages if m["group_id"]}
    missing_groups = groups_referenced - set(ctx.groups)
    if missing_groups:
        warnings.append(f"groups missing from groups.csv: {missing_groups}")

    biz_referenced = {m["business_id"] for m in ctx.messages if m["business_id"]}
    missing_biz = biz_referenced - set(ctx.business_accounts)
    if missing_biz:
        warnings.append(f"businesses missing from business_accounts.csv: {missing_biz}")

    for m in ctx.messages:
        media_id = m["media_id"]
        if not media_id:
            continue
        if media_id not in ctx.images and media_id not in ctx.voice_notes:
            warnings.append(f"message {m['message_id']} references unknown media_id {media_id}")

    for path in [r["file_path"] for r in ctx.images.values()] + [
        r["file_path"] for r in ctx.voice_notes.values()
    ]:
        full_path = config.DATASET_DIR / path
        if not full_path.exists():
            warnings.append(f"media file missing on disk: {path}")

    return warnings

"""Tier-1 deterministic validator: schema exactness + policy invariants.
Pure code, no LLM, runs on every build. See PLAN.md sec 8."""

import csv

from . import config, features
from .evidence import candidate_id_set
from .output import COLUMNS
from .rules import INJECTION_RE


def _load_rows(path):
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def validate_output(ctx, output_path=None) -> list:
    """Returns a list of violation strings. Empty list == clean."""
    output_path = output_path or config.OUTPUT_CSV
    violations = []

    with open(output_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
    if tuple(header) != COLUMNS:
        violations.append(f"header mismatch: expected {COLUMNS}, got {tuple(header)}")

    rows = _load_rows(output_path)
    row_ids = [r["message_id"] for r in rows]
    if len(row_ids) != len(set(row_ids)):
        violations.append("duplicate message_id rows in output.csv")

    expected_ids = {m["message_id"] for m in ctx.messages}
    got_ids = set(row_ids)
    missing = expected_ids - got_ids
    extra = got_ids - expected_ids
    if missing:
        violations.append(f"missing predictions for: {sorted(missing)}")
    if extra:
        violations.append(f"predictions for unknown message_ids: {sorted(extra)}")

    msgs_by_id = {m["message_id"]: m for m in ctx.messages}
    history_ids = set(ctx.message_history_by_id)

    for row in rows:
        mid = row["message_id"]
        if row["action"] not in config.ACTIONS:
            violations.append(f"{mid}: invalid action '{row['action']}'")
        if row["message_type"] not in config.MESSAGE_TYPES:
            violations.append(f"{mid}: invalid message_type '{row['message_type']}'")

        try:
            conf = float(row["confidence"])
        except ValueError:
            violations.append(f"{mid}: confidence '{row['confidence']}' is not a number")
            conf = None
        if conf is not None:
            if not (0 <= conf <= 1):
                violations.append(f"{mid}: confidence {conf} out of [0,1]")
            if row["confidence"] != f"{conf:.2f}":
                violations.append(f"{mid}: confidence '{row['confidence']}' not formatted to 2 decimals")

        ev_field = row["evidence_message_ids"]
        if ev_field != "none":
            ev_ids = ev_field.split(";")
            if len(ev_ids) > config.MAX_EVIDENCE:
                violations.append(f"{mid}: evidence_message_ids has {len(ev_ids)} > {config.MAX_EVIDENCE}")
            for eid in ev_ids:
                if eid not in history_ids:
                    violations.append(f"{mid}: evidence id '{eid}' not found in message_history.csv")

            msg = msgs_by_id.get(mid)
            if msg:
                bundle = features.build_feature_bundle(msg, ctx)
                pool = candidate_id_set(bundle, ctx)
                for eid in ev_ids:
                    if eid in history_ids and eid not in pool:
                        violations.append(
                            f"{mid}: evidence id '{eid}' has no lineage with this message "
                            "(not same sender/business/group/pattern)"
                        )

        # Policy invariants
        if row["message_type"] in ("scam", "spam") and row["action"] != "mute":
            violations.append(
                f"{mid}: message_type '{row['message_type']}' requires action=mute, got '{row['action']}'"
            )

        msg = msgs_by_id.get(mid)
        if msg:
            bundle = features.build_feature_bundle(msg, ctx)
            if (
                bundle.content["in_dnd_window"]
                and row["action"] == "notify"
                and row["message_type"] != "urgent"
                and bundle.conversation_type != "personal"
            ):
                violations.append(f"{mid}: notify inside DND window without urgent type or personal exemption")
            has_behavioral_evidence = (
                (bundle.behavior.get("reported_count") or 0) > 0
                or (bundle.behavior.get("muted_after_count") or 0) > 0
            )
            has_unambiguous_safety_signal = (
                bundle.trust.get("domain_mismatch")
                or bundle.content["entities"]["otp_or_fee_ask"]
                or INJECTION_RE.search(msg["message_text"] or "")
            )
            if (
                bundle.conversation_type == "personal"
                and row["action"] == "mute"
                and not has_behavioral_evidence
                and not has_unambiguous_safety_signal
            ):
                violations.append(
                    f"{mid}: personal sender muted without behavioral evidence or an unambiguous "
                    "safety signal (domain mismatch / OTP-fee ask / injection pattern)"
                )

    return violations

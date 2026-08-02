"""Eval harness: runs the pipeline against dataset/sample_messages.csv
(the 30 solved examples) and scores predictions against the gold labels.
Stratified 10 train / 20 test split by action, deterministic (sorted by
message_id, no randomness) so reruns are reproducible. See PLAN.md sec 8."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router import decide, loaders, perception  # noqa: E402
from router.transcribe import transcribe_all  # noqa: E402

from evaluation.calibration import calibration_report, print_calibration_report  # noqa: E402
from evaluation.cross_validation import cross_validate, print_cv_report  # noqa: E402

# Deprecated in favor of 5-fold stratified scoring (see cross_validation.py)
# -- a single fixed split showed TRAIN at 70% and TEST at 90% type accuracy,
# backwards from what overfitting would predict, which was the tell that
# n=10/n=20 single-split numbers are dominated by noise, not signal. Kept
# only because run_all_checks.py's per-item MISS/PUSHED listings are easier
# to read against one named split; the k-fold report is the primary numbers.
TRAIN_FRACTION = 10 / 30


def stratified_split(sample_rows):
    groups = {}
    for row in sample_rows:
        groups.setdefault(row["action"], []).append(row)
    train, test = [], []
    for action, rows in groups.items():
        rows_sorted = sorted(rows, key=lambda r: r["message_id"])
        n_train = round(len(rows_sorted) * TRAIN_FRACTION)
        train += rows_sorted[:n_train]
        test += rows_sorted[n_train:]
    return train, test


def _to_msg_dict(sample_row):
    keys = (
        "message_id", "user_id", "conversation_type", "group_id", "business_id",
        "sender_user_id", "created_at", "message_text", "media_type", "media_id",
        "forwarded_count",
    )
    return {k: sample_row.get(k, "") for k in keys}


def _evidence_set(field: str) -> set:
    if not field or field.strip().lower() == "none":
        return set()
    return {e.strip() for e in field.split(";") if e.strip()}


def _perceptions_for(gold_rows, transcripts) -> dict:
    """sample_messages.csv rows have their own message_ids, disjoint from
    ctx.messages -- perception.perceive_all() is scoped to ctx.messages, so
    samples get perceived directly here, keyed the same way."""
    results = {}
    for gold in gold_rows:
        msg = _to_msg_dict(gold)
        if msg["message_text"]:
            text = msg["message_text"]
        else:
            entry = transcripts.get(msg["media_id"]) if msg["media_id"] else None
            text = entry["transcript"] if entry else ""
        results[msg["message_id"]] = perception.perceive(text)
    return results


def evaluate(gold_rows, ctx, transcripts, label: str) -> dict:
    perceptions = _perceptions_for(gold_rows, transcripts)
    predictions = []
    for gold in gold_rows:
        msg = _to_msg_dict(gold)
        pred = decide.decide_message(msg, ctx, transcripts, perceptions)
        predictions.append((gold, pred))

    n = len(predictions)
    action_correct = sum(1 for g, p in predictions if g["action"] == p.action)
    type_correct = sum(1 for g, p in predictions if g["message_type"] == p.message_type)

    evidence_any_overlap = 0
    evidence_jaccard_sum = 0.0
    for g, p in predictions:
        gold_ev = _evidence_set(g["evidence_message_ids"])
        pred_ev = _evidence_set(p.evidence_message_ids)
        if not gold_ev and not pred_ev:
            evidence_any_overlap += 1
            evidence_jaccard_sum += 1.0
            continue
        inter = len(gold_ev & pred_ev)
        union = len(gold_ev | pred_ev) or 1
        if inter > 0:
            evidence_any_overlap += 1
        evidence_jaccard_sum += inter / union

    conf_abs_err = sum(abs(float(g["confidence"]) - p.confidence) for g, p in predictions) / n

    # Threshold 1 (hard line): never miss an important message -- gold says
    # notify, we said digest/mute.
    critical_misses = [
        (g["message_id"], g["action"], p.action)
        for g, p in predictions
        if g["action"] == "notify" and p.action != "notify"
    ]

    # Threshold 2 (sanity check, not a hard gate): no spam/scam pushed
    # forward -- gold says mute (esp. spam/scam type), we said notify. This
    # is the opposite failure mode from critical_misses: over-eager
    # notification of exactly the content the system exists to suppress.
    spam_pushed_forward = [
        (g["message_id"], g["message_type"], p.action)
        for g, p in predictions
        if g["action"] == "mute" and p.action == "notify"
    ]
    spam_scam_pushed_forward = [
        (mid, mtype, pred_a)
        for mid, mtype, pred_a in spam_pushed_forward
        if mtype in ("spam", "scam")
    ]

    confusion = Counter((g["action"], p.action) for g, p in predictions)

    report = {
        "label": label,
        "n": n,
        "action_accuracy": round(action_correct / n, 3),
        "message_type_accuracy": round(type_correct / n, 3),
        "evidence_any_overlap_rate": round(evidence_any_overlap / n, 3),
        "evidence_avg_jaccard": round(evidence_jaccard_sum / n, 3),
        "confidence_mae": round(conf_abs_err, 3),
        "critical_misses": critical_misses,
        "critical_miss_count": len(critical_misses),
        "spam_pushed_forward": spam_pushed_forward,
        "spam_pushed_forward_count": len(spam_pushed_forward),
        "spam_scam_pushed_forward_count": len(spam_scam_pushed_forward),
        "confusion": dict(confusion),
        "calibration": calibration_report(predictions),
    }
    return report


def print_report(report):
    print(f"--- {report['label']} (n={report['n']}) ---")
    print(f"action_accuracy:        {report['action_accuracy']}")
    print(f"message_type_accuracy:  {report['message_type_accuracy']}")
    print(f"evidence_any_overlap:   {report['evidence_any_overlap_rate']}")
    print(f"evidence_avg_jaccard:   {report['evidence_avg_jaccard']}")
    print(f"confidence_mae:         {report['confidence_mae']}")
    print(f"critical_miss_count:    {report['critical_miss_count']} (gold=notify, predicted!=notify) [HARD THRESHOLD]")
    if report["critical_misses"]:
        for mid, gold_a, pred_a in report["critical_misses"]:
            print(f"  MISS {mid}: gold=notify predicted={pred_a}")
    print(f"spam_pushed_forward:    {report['spam_pushed_forward_count']} (gold=mute, predicted=notify) [sanity check]")
    print(f"  of which spam/scam:   {report['spam_scam_pushed_forward_count']}")
    if report["spam_pushed_forward"]:
        for mid, gold_type, pred_a in report["spam_pushed_forward"]:
            print(f"  PUSHED {mid}: gold=mute/{gold_type} predicted={pred_a}")
    print(f"confusion (gold,pred):  {report['confusion']}")
    print_calibration_report(report["calibration"], report["label"])


def main():
    ctx = loaders.load_context()
    transcripts = transcribe_all(ctx)
    cv = cross_validate(evaluate, ctx.sample_messages, ctx, transcripts, k=5)
    print_cv_report(cv)
    return cv


if __name__ == "__main__":
    main()

"""Eval harness: runs the pipeline against dataset/sample_messages.csv
(the 30 solved examples) and scores predictions against the gold labels.
Stratified 10 train / 20 test split by action, deterministic (sorted by
message_id, no randomness) so reruns are reproducible. See PLAN.md sec 8."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router import decide, loaders  # noqa: E402
from router.transcribe import transcribe_all  # noqa: E402

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


def evaluate(gold_rows, ctx, transcripts, label: str) -> dict:
    predictions = []
    for gold in gold_rows:
        msg = _to_msg_dict(gold)
        pred = decide.decide_message(msg, ctx, transcripts)
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

    # Headline: never miss an important message -- gold says notify, we said digest/mute.
    critical_misses = [
        (g["message_id"], g["action"], p.action)
        for g, p in predictions
        if g["action"] == "notify" and p.action != "notify"
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
        "confusion": dict(confusion),
    }
    return report


def print_report(report):
    print(f"--- {report['label']} (n={report['n']}) ---")
    print(f"action_accuracy:        {report['action_accuracy']}")
    print(f"message_type_accuracy:  {report['message_type_accuracy']}")
    print(f"evidence_any_overlap:   {report['evidence_any_overlap_rate']}")
    print(f"evidence_avg_jaccard:   {report['evidence_avg_jaccard']}")
    print(f"confidence_mae:         {report['confidence_mae']}")
    print(f"critical_miss_count:    {report['critical_miss_count']} (gold=notify, predicted!=notify)")
    if report["critical_misses"]:
        for mid, gold_a, pred_a in report["critical_misses"]:
            print(f"  MISS {mid}: gold=notify predicted={pred_a}")
    print(f"confusion (gold,pred):  {report['confusion']}")


def main():
    ctx = loaders.load_context()
    transcripts = transcribe_all(ctx)
    train, test = stratified_split(ctx.sample_messages)
    print(f"split: {len(train)} train / {len(test)} test (of {len(ctx.sample_messages)} samples)")
    train_report = evaluate(train, ctx, transcripts, "TRAIN")
    test_report = evaluate(test, ctx, transcripts, "TEST (frozen)")
    print_report(train_report)
    print()
    print_report(test_report)
    return train_report, test_report


if __name__ == "__main__":
    main()

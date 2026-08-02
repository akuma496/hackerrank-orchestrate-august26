"""Confidence calibration measurement: Expected Calibration Error and Brier
score against the 30 solved samples. This replaces "the bands were chosen
to match the sample range" with an actual measurement of whether stated
confidence tracks actual correctness.

Caveat stated once, honestly, and not repeated everywhere: n=30 total (and
smaller still per cross-validation fold, see cross_validation.py) makes any
calibration curve here noisy. This measures and reports what we have; it
does not claim statistical significance."""

from collections import defaultdict


def brier_score(predictions: list) -> float:
    """predictions: [(confidence, correct_bool), ...]. Mean squared error
    between stated confidence and the binary outcome. Lower is better;
    0 = perfect, 0.25 = the score of always predicting 0.5."""
    if not predictions:
        return None
    return round(sum((c - int(correct)) ** 2 for c, correct in predictions) / len(predictions), 4)


def expected_calibration_error(predictions: list, n_bins: int = 5) -> dict:
    """Bins by confidence value (not by rank), compares average stated
    confidence to actual accuracy within each bin. ECE is the bin-size
    weighted mean absolute gap. Returns the per-bin breakdown too, since
    the aggregate number alone hides whether we're over- or under-confident."""
    if not predictions:
        return {"ece": None, "bins": []}

    bin_width = 1.0 / n_bins
    buckets = defaultdict(list)
    for conf, correct in predictions:
        idx = min(int(conf / bin_width), n_bins - 1)
        buckets[idx].append((conf, correct))

    bins = []
    n_total = len(predictions)
    ece = 0.0
    for idx in sorted(buckets):
        items = buckets[idx]
        avg_conf = sum(c for c, _ in items) / len(items)
        accuracy = sum(int(ok) for _, ok in items) / len(items)
        weight = len(items) / n_total
        gap = abs(avg_conf - accuracy)
        ece += weight * gap
        bins.append({
            "range": f"{idx * bin_width:.2f}-{(idx + 1) * bin_width:.2f}",
            "n": len(items),
            "avg_confidence": round(avg_conf, 3),
            "accuracy": round(accuracy, 3),
            "gap": round(gap, 3),
        })
    return {"ece": round(ece, 4), "bins": bins}


def calibration_report(predictions_with_gold: list) -> dict:
    """predictions_with_gold: [(gold_row, DecisionRow), ...]. Builds the
    (confidence, correct) pairs from action-correctness and reports Brier +
    ECE, plus mean confidence vs actual accuracy for the honest one-line
    summary (over/under-confident, and by how much)."""
    pairs = [(p.confidence, g["action"] == p.action) for g, p in predictions_with_gold]
    if not pairs:
        return {"brier": None, "ece": None, "bins": [], "mean_confidence": None, "actual_accuracy": None, "direction": "n/a"}

    mean_conf = sum(c for c, _ in pairs) / len(pairs)
    actual_acc = sum(int(ok) for _, ok in pairs) / len(pairs)
    gap = mean_conf - actual_acc
    direction = "over-confident" if gap > 0.02 else ("under-confident" if gap < -0.02 else "well-calibrated")

    ece_result = expected_calibration_error(pairs)
    return {
        "brier": brier_score(pairs),
        "ece": ece_result["ece"],
        "bins": ece_result["bins"],
        "mean_confidence": round(mean_conf, 3),
        "actual_accuracy": round(actual_acc, 3),
        "gap": round(gap, 3),
        "direction": direction,
    }


def print_calibration_report(report: dict, label: str) -> None:
    print(f"--- calibration: {label} ---")
    if report["brier"] is None:
        print("  (no predictions)")
        return
    print(f"  brier_score:       {report['brier']}  (0=perfect, 0.25=coin-flip baseline)")
    print(f"  ECE:               {report['ece']}")
    print(f"  mean confidence:   {report['mean_confidence']}")
    print(f"  actual accuracy:   {report['actual_accuracy']}")
    print(f"  verdict:           {report['direction']} (gap={report['gap']:+.3f})")
    for b in report["bins"]:
        print(f"    [{b['range']}] n={b['n']:2d}  avg_conf={b['avg_confidence']}  accuracy={b['accuracy']}  gap={b['gap']}")

"""Constants sensitivity sweep: for each previously-unvalidated threshold
constant in rules.py/evidence.py, sweep a range of values and measure how
much the RULE-FIRING pattern across all 110 messages changes.

Deliberately cheap and LLM-free: this measures which/how-many hard rules
fire, not full-pipeline accuracy (that would require re-running the LLM for
every message that changes path at every sweep point -- possible but slow
and not necessary for the actual question, which is structural: is this
constant load-bearing, or arbitrary).

Reading the output: a FLAT rule-fire count across a wide range means the
exact value doesn't matter much -- safe, not fitted to this corpus. A SHARP
step right at the current default is the signature of a threshold that was
implicitly tuned (even if only by "this felt right while looking at the
data") rather than derived from a real cutoff.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router import evidence, features, loaders, rules  # noqa: E402


def _rule_fire_signature(ctx):
    """message_id -> rule source fired (or None), for every message. Pure
    and cheap: no LLM calls, just rules + features over already-loaded data."""
    sig = {}
    for msg in ctx.messages:
        bundle = features.build_feature_bundle(msg, ctx)
        verdict = rules.apply_hard_rules(bundle, ctx)
        sig[msg["message_id"]] = verdict.source if verdict else None
    return sig


def sweep_constant(ctx, module, attr_name: str, values: list, baseline_sig: dict) -> list:
    original = getattr(module, attr_name)
    rows = []
    try:
        for v in values:
            setattr(module, attr_name, v)
            sig = _rule_fire_signature(ctx)
            fired = sum(1 for s in sig.values() if s is not None)
            changed = sum(1 for mid in sig if sig[mid] != baseline_sig[mid])
            rows.append({"value": v, "rules_fired": fired, "changed_vs_baseline": changed})
    finally:
        setattr(module, attr_name, original)
    return rows


SWEEPS = [
    ("rules.HR2_YOUNG_DOMAIN_DAYS", rules, "HR2_YOUNG_DOMAIN_DAYS", [7, 15, 30, 60, 90, 180]),
    ("rules.HR5_DISMISSAL_RATE_THRESHOLD", rules, "HR5_DISMISSAL_RATE_THRESHOLD", [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]),
    ("rules._LEGIT_MIN_ACCOUNT_AGE_DAYS", rules, "_LEGIT_MIN_ACCOUNT_AGE_DAYS", [30, 90, 120, 180, 270, 365]),
    ("rules._LEGIT_MIN_DOMAIN_AGE_DAYS", rules, "_LEGIT_MIN_DOMAIN_AGE_DAYS", [15, 30, 60, 90, 120, 180]),
    ("rules._LEGIT_MAX_REPORTS_30D", rules, "_LEGIT_MAX_REPORTS_30D", [3, 5, 10, 15, 20, 30]),
    ("evidence.NEAR_DUPLICATE_SIMILARITY_THRESHOLD", evidence, "NEAR_DUPLICATE_SIMILARITY_THRESHOLD", [0.2, 0.25, 0.3, 0.35, 0.45, 0.55]),
]


def run_sweep():
    ctx = loaders.load_context()
    baseline_sig = _rule_fire_signature(ctx)
    baseline_fired = sum(1 for s in baseline_sig.values() if s is not None)
    print(f"baseline: {baseline_fired}/110 messages rule-decided\n")

    results = {}
    for label, module, attr, values in SWEEPS:
        default = getattr(module, attr)
        print(f"--- {label} (default={default}) ---")
        rows = sweep_constant(ctx, module, attr, values, baseline_sig)
        fire_counts = [r["rules_fired"] for r in rows]
        spread = max(fire_counts) - min(fire_counts)
        verdict = "FLAT (not load-bearing)" if spread <= 2 else (
            "MODERATE sensitivity" if spread <= 5 else "SHARP -- corpus-fitted, needs justification"
        )
        for r in rows:
            marker = " <- default" if r["value"] == default else ""
            print(f"  value={r['value']:<6} fired={r['rules_fired']:3d}/110  changed_vs_baseline={r['changed_vs_baseline']:3d}{marker}")
        print(f"  spread={spread}  verdict: {verdict}\n")
        results[label] = {"rows": rows, "spread": spread, "verdict": verdict}
    return results


if __name__ == "__main__":
    run_sweep()

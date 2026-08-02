"""Single entry point for 'did this change break anything': regenerates
output.csv, runs the Tier-1 validator, the eval harness (train+test), the
Tier-2 judge sweep, and a determinism rerun -- in that order, every time.

This exists specifically to prevent evaluator drift: the judge, the
validator, and the eval harness must always be checked against the CURRENT
pipeline output, not a stale copy from a previous run. Run this after any
change to rules.py, features.py, config.py, or any prompts/*.md file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router import decide, loaders, output, perception, validate  # noqa: E402

from evaluation import judge  # noqa: E402
from evaluation.main import evaluate  # noqa: E402
from evaluation.cross_validation import cross_validate, print_cv_report  # noqa: E402


def main():
    ctx = loaders.load_context()

    print("=== 1/5: regenerating output.csv ===")
    from router.transcribe import transcribe_all

    transcripts = transcribe_all(ctx)
    rows = decide.decide_all(ctx)
    output.write_output(rows)
    print(f"wrote {len(rows)} rows")
    if decide.CLAMP_EVENTS:
        print(f"CLAMP EVENTS (LLM/rule returned an out-of-vocabulary value): {len(decide.CLAMP_EVENTS)}")
        for e in decide.CLAMP_EVENTS:
            print(" -", e)
    else:
        print("clamp events: 0")

    perceptions = perception.perceive_all(ctx, transcripts)
    stats = perception.agreement_stats(ctx, transcripts, perceptions)
    print(
        f"perception vs regex: {stats['both_agree_flagged']} both-flagged, "
        f"{stats['both_agree_clear']} both-clear, "
        f"{len(stats['regex_only'])} regex-only, "
        f"{len(stats['perception_only'])} perception-only (capped at 'weak' authority)"
    )
    if stats["perception_only"]:
        print("  perception-only:", ", ".join(stats["perception_only"]))

    print("\n=== 2/5: Tier-1 validator ===")
    violations = validate.validate_output(ctx)
    if violations:
        print(f"FAILED: {len(violations)} violation(s)")
        for v in violations:
            print(" -", v)
    else:
        print("clean: 0 violations")

    print("\n=== 3/5: eval harness (sample_messages.csv, 5-fold stratified) ===")
    cv = cross_validate(evaluate, ctx.sample_messages, ctx, transcripts, k=5)
    print_cv_report(cv)

    print("\n=== 4/5: Tier-2 judge sweep (advisory) ===")
    flags = judge.run_sweep()
    print(f"{len(flags)} flag(s) out of {len(rows)} rows")
    for f in flags:
        print(f"  {f['message_id']} [{f['action']}/{f['message_type']}]: {f['flag_reason']}")

    print("\n=== 5/5: determinism check (rerun from warm cache) ===")
    rows_rerun = decide.decide_all(ctx)
    output.write_output(rows_rerun, config_output_path := Path("cache/_determinism_check.csv"))
    with open(output.config.OUTPUT_CSV, "rb") as f1, open(config_output_path, "rb") as f2:
        identical = f1.read() == f2.read()
    config_output_path.unlink()
    print("byte-identical rerun: CONFIRMED" if identical else "byte-identical rerun: FAILED")

    print("\n=== summary ===")
    print(f"validator violations:       {len(violations)}")
    print(f"action_accuracy (pooled n={cv['total_n']}): {cv['pooled_action_accuracy']} (95% CI {cv['pooled_action_ci95']})")
    print(f"type_accuracy   (pooled n={cv['total_n']}): {cv['pooled_type_accuracy']} (95% CI {cv['pooled_type_ci95']})")
    print(f"action_accuracy across folds: mean={cv['action_accuracy_mean']} std={cv['action_accuracy_std']}")
    print(f"critical misses (pooled, all 30): {cv['total_critical_misses']} [HARD THRESHOLD: 0]")
    print(f"spam pushed fwd (pooled, all 30): {cv['total_spam_pushed_forward']} [sanity check]")
    print(f"judge flags:                {len(flags)}")
    print(f"determinism:                {'OK' if identical else 'BROKEN'}")

    return {
        "violations": violations,
        "cross_validation": cv,
        "judge_flags": flags,
        "deterministic": identical,
    }


if __name__ == "__main__":
    main()

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

from router import decide, loaders, output, validate  # noqa: E402

from evaluation import judge  # noqa: E402
from evaluation.main import evaluate, print_report, stratified_split  # noqa: E402


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

    print("\n=== 2/5: Tier-1 validator ===")
    violations = validate.validate_output(ctx)
    if violations:
        print(f"FAILED: {len(violations)} violation(s)")
        for v in violations:
            print(" -", v)
    else:
        print("clean: 0 violations")

    print("\n=== 3/5: eval harness (sample_messages.csv) ===")
    train, test = stratified_split(ctx.sample_messages)
    train_report = evaluate(train, ctx, transcripts, "TRAIN")
    test_report = evaluate(test, ctx, transcripts, "TEST (frozen)")
    print_report(train_report)
    print()
    print_report(test_report)

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
    print(f"validator violations:     {len(violations)}")
    print(f"train action_accuracy:    {train_report['action_accuracy']}")
    print(f"test  action_accuracy:    {test_report['action_accuracy']}")
    print(f"train type_accuracy:      {train_report['message_type_accuracy']}")
    print(f"test  type_accuracy:      {test_report['message_type_accuracy']}")
    print(f"critical misses (t/te):   {train_report['critical_miss_count']}/{test_report['critical_miss_count']} [HARD THRESHOLD: 0]")
    print(f"spam pushed fwd (t/te):   {train_report['spam_pushed_forward_count']}/{test_report['spam_pushed_forward_count']} [sanity check]")
    print(f"judge flags:              {len(flags)}")
    print(f"determinism:              {'OK' if identical else 'BROKEN'}")

    return {
        "violations": violations,
        "train_report": train_report,
        "test_report": test_report,
        "judge_flags": flags,
        "deterministic": identical,
    }


if __name__ == "__main__":
    main()

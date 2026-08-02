"""Entry point: python main.py

Reads dataset/messages.csv, routes every message through the pipeline
(hard rules -> certainty engine -> cached LLM decision -> policy guards),
and writes dataset/output.csv.

Run with a warm cache (code/cache/*.json, committed to the repo) and this
makes zero API calls -- fully offline, byte-identical to the version this
was submitted with. A cold cache needs GEMINI_API_KEY (media transcription)
and ANTHROPIC_API_KEY (decisions/perception/judge) set via environment
variables or a .env file; see README.md.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from router import decide, loaders, output, validate  # noqa: E402


def main():
    print("Loading dataset...")
    ctx = loaders.load_context()
    if ctx.integrity_warnings:
        print(f"WARNING: {len(ctx.integrity_warnings)} data integrity warning(s):")
        for w in ctx.integrity_warnings:
            print(" -", w)

    print(f"Routing {len(ctx.messages)} messages...")
    t0 = time.time()
    rows = decide.decide_all(ctx)
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s.")

    output.write_output(rows)
    print(f"Wrote {len(rows)} predictions to {output.config.OUTPUT_CSV}")

    if decide.CLAMP_EVENTS:
        print(f"NOTE: {len(decide.CLAMP_EVENTS)} value(s) were coerced to a safe default -- see below.")
        for e in decide.CLAMP_EVENTS:
            print(" -", e)

    violations = validate.validate_output(ctx)
    if violations:
        print(f"VALIDATION FAILED: {len(violations)} violation(s):")
        for v in violations:
            print(" -", v)
        sys.exit(1)
    print("Validation passed: output.csv is schema-clean and policy-consistent.")


if __name__ == "__main__":
    main()

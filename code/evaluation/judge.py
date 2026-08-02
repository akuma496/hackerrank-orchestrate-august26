"""Tier-2 LLM judge: advisory-only semantic coherence check over the final
output.csv. Never edits a decision -- produces a flag list for human review
before freeze. Cached like every other LLM call, so a rerun with no code
changes costs zero API calls. See PLAN.md sec 8."""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router import config, decide, evidence, features, loaders  # noqa: E402
from router.cache_utils import load_cache, save_cache, text_hash  # noqa: E402
from router.transcribe import transcribe_all  # noqa: E402

def _render_system_facts() -> str:
    """Built from the live config, not hand-typed prose -- if ACTIONS,
    MESSAGE_TYPES, or TYPE_PRECEDENCE ever change, the judge's understanding
    of the system updates automatically instead of silently going stale
    (the failure mode that produced 78 false-positive flags on the first
    sweep: the judge didn't know the system's own action taxonomy)."""
    return (
        f"Valid actions in this system (exhaustive, no others exist): {', '.join(config.ACTIONS)}.\n"
        f"Valid message_types (exhaustive): {', '.join(config.MESSAGE_TYPES)}.\n"
        f"message_type precedence, highest to lowest: {' > '.join(config.TYPE_PRECEDENCE)}."
    )


def _build_judge_system_prompt() -> str:
    template = (config.PROMPTS_DIR / "judge.md").read_text(encoding="utf-8")
    return template.replace("{{SYSTEM_FACTS}}", _render_system_facts())


_JUDGE_PROMPT = _build_judge_system_prompt()

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic

        api_key = config.get_env(config.ANTHROPIC_API_KEY_ENV)
        if not api_key:
            raise RuntimeError(f"{config.ANTHROPIC_API_KEY_ENV} is not set.")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _call_judge(payload_text: str) -> dict:
    client = _get_client()
    response = client.messages.create(
        model=config.CLAUDE_JUDGE_MODEL,
        max_tokens=512,
        system=_JUDGE_PROMPT,
        messages=[{"role": "user", "content": payload_text}],
    )
    text_block = next((b for b in response.content if b.type == "text"), None)
    text = text_block.text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def judge_call(payload_text: str) -> dict:
    cache = load_cache(config.JUDGE_FLAGS_CACHE)
    key = text_hash(f"{config.CLAUDE_JUDGE_MODEL}|{_JUDGE_PROMPT}|{payload_text}")
    if key in cache:
        return cache[key]
    result = _call_judge(payload_text)
    cache[key] = result
    save_cache(config.JUDGE_FLAGS_CACHE, cache)
    return result


def _build_payload(msg: dict, row: dict, ctx, transcripts: dict) -> str:
    """Gives the judge the SAME deterministic context the original decision
    saw (features + ranked evidence with event summaries), not a weaker
    reconstruction -- otherwise the judge flags things the decision-maker
    actually had grounds for."""
    entry = transcripts.get(msg["media_id"]) if msg["media_id"] else None
    transcript_text = entry["transcript"] if entry else None
    transcript_entities = entry["entities"] if entry else None

    bundle = features.build_feature_bundle(msg, ctx, transcript_entities=transcript_entities)
    evidence_items = evidence.rank_evidence(bundle, ctx, transcript_text=transcript_text)

    content = msg["message_text"] or transcript_text or f"(media message, media_type={msg['media_type']})"
    parts = [
        f"MESSAGE CONTENT: {content}",
        "",
        "FEATURES (the deterministic context available to the decision-maker):",
        decide._format_features_block(bundle),
        "",
        f"DECISION: action={row['action']}, message_type={row['message_type']}",
        f"REASON: {row['reason']}",
        "",
        "EVIDENCE CANDIDATES THAT WERE AVAILABLE (decision may cite a subset):",
        decide._format_evidence_block(evidence_items),
        "",
        f"EVIDENCE ACTUALLY CITED IN THE DECISION: {row['evidence_message_ids']}",
    ]
    return "\n".join(parts)


def run_sweep(output_path=None) -> list:
    """Runs the judge over every row in output.csv. Returns a list of flags
    (dicts with message_id, action, message_type, reason, flag_reason)."""
    output_path = output_path or config.OUTPUT_CSV
    ctx = loaders.load_context()
    transcripts = transcribe_all(ctx)
    msgs_by_id = {m["message_id"]: m for m in ctx.messages}

    with open(output_path, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    flags = []
    for row in rows:
        msg = msgs_by_id.get(row["message_id"])
        if not msg:
            continue
        payload = _build_payload(msg, row, ctx, transcripts)
        verdict = judge_call(payload)
        if not verdict.get("coherent", True):
            flags.append(
                {
                    "message_id": row["message_id"],
                    "action": row["action"],
                    "message_type": row["message_type"],
                    "reason": row["reason"],
                    "flag_reason": verdict.get("flag_reason", ""),
                }
            )
    return flags


if __name__ == "__main__":
    flags = run_sweep()
    print(f"judge sweep complete: {len(flags)} flag(s) out of 110 rows")
    for f in flags:
        print(f"--- {f['message_id']} [{f['action']}/{f['message_type']}]")
        print(f"  reason:      {f['reason']}")
        print(f"  flag_reason: {f['flag_reason']}")

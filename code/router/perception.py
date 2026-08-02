"""Additive LLM perception layer: catches the same underlying facts regex
catches, but generalizes across phrasing and language in a way a keyword
list structurally cannot (see msg_096, a benign message in French -- every
regex in this codebase is English/romanized-Hindi only, so a French scam
would sail past HR1/HR2 entirely and only this layer, or the full LLM
decision path, would catch it).

Deliberately scoped to ONE signal (credential/payment-request detection --
the most heavily patched regex this session) plus language, not a general
re-classification. Deliberately excludes injection detection: asking an LLM
"is this trying to manipulate you" invites the exact confusion an injection
attempt is designed to cause (see adversarial review). Injection stays
regex-only, on purpose.

Additive: this SUPPLEMENTS regex via OR-merge (see features.py), it never
replaces it. If perception is unavailable (no cache, no API key), the
pipeline runs exactly as it did before this file existed -- regex is always
computed first and perception only ever adds true, never removes a regex
finding. Cached by content hash like every other LLM call, so a warm cache
costs zero API calls and reruns are byte-identical."""

import json

from . import config
from .cache_utils import load_cache, save_cache, text_hash

_PERCEIVE_PROMPT = (config.PROMPTS_DIR / "perceive.md").read_text(encoding="utf-8")
_EMPTY = {"credential_or_payment_request": False, "language": "en"}

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic

        api_key = config.get_env(config.ANTHROPIC_API_KEY_ENV)
        if not api_key:
            raise RuntimeError(
                f"{config.ANTHROPIC_API_KEY_ENV} is not set. Set it in your environment "
                "or a .env file to run perception (cache misses only)."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _call_perceive(text: str) -> dict:
    client = _get_client()
    response = client.messages.create(
        model=config.CLAUDE_JUDGE_MODEL,  # cheap/fast tier; this is a narrow extraction, not a judgment call
        max_tokens=128,
        temperature=config.TEMPERATURE,
        system=_PERCEIVE_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    text_block = next((b for b in response.content if b.type == "text"), None)
    body = text_block.text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        if body.startswith("json"):
            body = body[4:]
    parsed = json.loads(body)
    return {**_EMPTY, **parsed}


def perceive(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return dict(_EMPTY)
    cache = load_cache(config.PERCEPTIONS_CACHE)
    key = text_hash(f"{_PERCEIVE_PROMPT}|{text}")
    if key in cache:
        return cache[key]
    result = _call_perceive(text)
    cache[key] = result
    save_cache(config.PERCEPTIONS_CACHE, cache)
    return result


def perceive_all(ctx, transcripts: dict) -> dict:
    """Returns message_id -> perception dict, for every message's own text
    (or its media transcript, for media messages)."""
    results = {}
    for msg in ctx.messages:
        if msg["message_text"]:
            text = msg["message_text"]
        elif msg["media_id"] and msg["media_id"] in transcripts:
            text = transcripts[msg["media_id"]]["transcript"]
        else:
            text = ""
        results[msg["message_id"]] = perceive(text)
    return results


def agreement_stats(ctx, transcripts: dict, perceptions: dict) -> dict:
    """Passive drift visibility (same purpose as decide.CLAMP_EVENTS): how
    often does perception disagree with regex, and in which direction.
    Regex-only findings are the trusted baseline; perception-only findings
    are the ones capped at 'weak' authority (see features.py) precisely
    because this number is not zero."""
    from . import features

    perception_only, regex_only, both, neither = [], [], 0, 0
    for msg in ctx.messages:
        text = msg["message_text"]
        if not text and msg["media_id"] and msg["media_id"] in transcripts:
            text = transcripts[msg["media_id"]]["transcript"]
        regex_flag = features.extract_entities(text)["otp_or_fee_ask"] if text else False
        perc_flag = bool(perceptions.get(msg["message_id"], {}).get("credential_or_payment_request"))
        if regex_flag and perc_flag:
            both += 1
        elif regex_flag:
            regex_only.append(msg["message_id"])
        elif perc_flag:
            perception_only.append(msg["message_id"])
        else:
            neither += 1
    return {
        "both_agree_flagged": both,
        "both_agree_clear": neither,
        "regex_only": regex_only,
        "perception_only": perception_only,
    }

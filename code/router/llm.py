"""Claude decision call: structured JSON action/type/reason/evidence.
Cached by content hash of (model, prompt version, payload) so a warm cache
never calls the API and reruns are byte-identical."""

import json

from . import config
from .cache_utils import load_cache, save_cache, text_hash

_DECIDE_PROMPT = (config.PROMPTS_DIR / "decide.md").read_text(encoding="utf-8")

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic

        api_key = config.get_env(config.ANTHROPIC_API_KEY_ENV)
        if not api_key:
            raise RuntimeError(
                f"{config.ANTHROPIC_API_KEY_ENV} is not set. Set it in your environment "
                "or a .env file to run decisions (cache misses only)."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _call_claude(payload_text: str, model: str) -> dict:
    # claude-sonnet-5 rejects an explicit `temperature` param ("deprecated
    # for this model") -- omitted for that model only. claude-haiku-4-5
    # accepts it and we set it to 0: the cache is what guarantees a rerun
    # never calls the API again, but temperature=0 is what makes the FIRST
    # (cache-miss) call itself as reproducible as the model allows, rather
    # than pinning whatever answer happened to come back once. Escalation
    # (Sonnet) has no such guarantee and that gap is real, not papered over
    # -- see PLAN.md / README known-limitations.
    client = _get_client()
    kwargs = dict(
        model=model,
        max_tokens=1024,
        system=_DECIDE_PROMPT,
        messages=[{"role": "user", "content": payload_text}],
    )
    if model != config.CLAUDE_ESCALATE_MODEL:
        kwargs["temperature"] = config.TEMPERATURE
    response = client.messages.create(**kwargs)
    # Some models (e.g. claude-sonnet-5) may prepend a ThinkingBlock before
    # the TextBlock -- find the actual text content rather than assuming index 0.
    text_block = next((b for b in response.content if b.type == "text"), None)
    if text_block is None:
        raise RuntimeError(f"no text block in Claude response: {response.content}")
    text = text_block.text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def decide_call(payload_text: str, escalate: bool = False) -> dict:
    model = config.CLAUDE_ESCALATE_MODEL if escalate else config.CLAUDE_DECIDE_MODEL
    cache = load_cache(config.LLM_DECISIONS_CACHE)
    key = text_hash(f"{model}|{_DECIDE_PROMPT}|{payload_text}")

    if key in cache:
        return cache[key]

    result = _call_claude(payload_text, model)
    cache[key] = result
    save_cache(config.LLM_DECISIONS_CACHE, cache)
    return result

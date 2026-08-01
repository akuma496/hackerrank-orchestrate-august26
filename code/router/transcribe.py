"""Media transcription (OCR for images, ASR for voice notes) via Gemini.
Pure: receives only the media file, no user/conversation context. Every
result is cached to disk keyed by a hash of the file bytes + prompt, so a
warm cache never calls the API and reruns are byte-identical."""

import json
import mimetypes

from . import config
from .cache_utils import content_hash, load_cache, save_cache

_PROMPT = (config.PROMPTS_DIR / "transcribe.md").read_text(encoding="utf-8")
_EMPTY_ENTITIES = {"urls": [], "amounts": [], "brands": [], "dates": [], "otp_or_fee_ask": False}

_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai

        api_key = config.get_env(config.GEMINI_API_KEY_ENV)
        if not api_key:
            raise RuntimeError(
                f"{config.GEMINI_API_KEY_ENV} is not set. Set it in your environment "
                "or a .env file to transcribe media (cache misses only)."
            )
        _client = genai.Client(api_key=api_key)
    return _client


def _call_gemini(file_bytes: bytes, mime_type: str) -> dict:
    from google.genai import types

    client = _get_client()
    response = client.models.generate_content(
        model=config.GEMINI_TRANSCRIBE_MODEL,
        contents=[
            types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
            _PROMPT,
        ],
        config=types.GenerateContentConfig(
            temperature=config.TEMPERATURE,
            response_mime_type="application/json",
        ),
    )
    parsed = json.loads(response.text)
    entities = {**_EMPTY_ENTITIES, **parsed.get("entities", {})}
    return {"transcript": parsed.get("transcript", ""), "entities": entities}


def transcribe(media_id: str, file_path, media_kind: str) -> dict:
    """Returns {"transcript": str, "entities": {...}}. Cached by file content hash."""
    cache = load_cache(config.TRANSCRIPTS_CACHE)
    file_bytes = file_path.read_bytes()
    key = content_hash(file_bytes)

    if key in cache:
        return cache[key]

    mime_type = mimetypes.guess_type(str(file_path))[0] or (
        "image/jpeg" if media_kind == "image" else "audio/mpeg"
    )
    result = _call_gemini(file_bytes, mime_type)
    cache[key] = result
    save_cache(config.TRANSCRIPTS_CACHE, cache)
    return result


def transcribe_all(ctx) -> dict:
    """Transcribes every image/voice_note referenced in images.csv /
    voice_notes.csv. Returns media_id -> {"transcript", "entities"}."""
    results = {}
    for image_id, row in ctx.images.items():
        path = config.DATASET_DIR / row["file_path"]
        results[image_id] = transcribe(image_id, path, "image")
    for vn_id, row in ctx.voice_notes.items():
        path = config.DATASET_DIR / row["file_path"]
        results[vn_id] = transcribe(vn_id, path, "voice")
    return results

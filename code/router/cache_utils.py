"""Shared content-hash cache: read/write a JSON dict on disk, keyed by a
hash of whatever uniquely identifies the cached call (file bytes, or a
prompt+payload string). This is what makes reruns byte-identical and lets
graders run with zero API calls once the cache is populated."""

import hashlib
import json
from pathlib import Path


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_hash(text: str) -> str:
    return content_hash(text.encode("utf-8"))


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")

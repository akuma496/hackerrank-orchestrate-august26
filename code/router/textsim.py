"""Deterministic text similarity (token-set Jaccard). No embeddings, no ML --
this is the retrieval mechanism for evidence ranking and near-duplicate
detection, chosen because 1,063 history rows need no vector index."""

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "in", "on",
    "and", "or", "for", "at", "this", "that", "it", "be", "as", "by", "we",
    "you", "your", "i", "if", "so", "will", "with", "from", "has", "have",
}


def tokenize(text: str) -> set:
    tokens = _TOKEN_RE.findall((text or "").lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 2}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return round(inter / union, 3) if union else 0.0

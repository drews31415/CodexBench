"""Shared deterministic lexical retrieval for context candidates."""

import math
import re
from collections import Counter


BM25_VERSION = "bm25-unicode-word-v1"
K1 = 1.5
B = 0.75


def tokens(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\w+", text.casefold()))


def relevance(query: str, documents: list[str]) -> list[float]:
    if not documents:
        return []
    words = [Counter(tokens(document)) for document in documents]
    lengths = [sum(document.values()) for document in words]
    average = sum(lengths) / len(lengths) or 1.0
    terms = sorted(set(tokens(query)))
    idf = {}
    for term in terms:
        df = sum(term in document for document in words)
        idf[term] = math.log1p((len(words) - df + 0.5) / (df + 0.5))
    scores = []
    for document, length in zip(words, lengths, strict=True):
        norm = K1 * (1 - B + B * length / average)
        scores.append(sum(idf[term] * count * (K1 + 1) / (count + norm)
                          for term in terms if (count := document[term])))
    maximum = max(scores) or 1.0
    return [max(0.0, min(1.0, score / maximum)) for score in scores]

"""Fuzzy name matching utilities for cross-source enrichment."""

from __future__ import annotations

from difflib import SequenceMatcher

from src.enrichment.merger import normalize_name


def fuzzy_match_score(name_a: str, name_b: str) -> float:
    """Return similarity score (0.0-1.0) between two association names."""
    norm_a = normalize_name(name_a)
    norm_b = normalize_name(name_b)
    if not norm_a or not norm_b:
        return 0.0
    return SequenceMatcher(None, norm_a, norm_b).ratio()


def find_best_match(
    target_name: str,
    candidates: list[dict],
    name_key: str = "name",
    threshold: float = 0.80,
) -> tuple[dict | None, float]:
    """
    Find the best fuzzy match from a list of candidate dicts.
    Returns (best_match_dict, score) or (None, 0.0).
    """
    best_score = 0.0
    best_match = None
    for candidate in candidates:
        cand_name = candidate.get(name_key, "")
        if not cand_name:
            continue
        score = fuzzy_match_score(target_name, cand_name)
        if score > best_score and score >= threshold:
            best_score = score
            best_match = candidate
    return best_match, best_score

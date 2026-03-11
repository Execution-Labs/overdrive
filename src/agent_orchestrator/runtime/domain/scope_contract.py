"""Canonical scope-contract normalisation for task metadata.

Every call-site that reads or writes ``scope_contract`` metadata should
funnel through :func:`normalize_scope_contract` so that the shape is
consistent across the API layer, orchestrator service, and worker adapters.
"""

from __future__ import annotations

from typing import Any

SCOPE_CONTRACT_MODES: set[str] = {"restricted", "open"}


def _normalize_path_list(raw: Any) -> list[str]:
    """Deduplicate and clean a list of glob strings."""
    values = raw if isinstance(raw, list) else []
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def normalize_scope_contract(raw: Any) -> dict[str, Any] | None:
    """Normalize task scope metadata into a stable contract shape.

    Returns ``None`` when *raw* is not a ``dict``.  Otherwise returns a
    normalised copy that always contains ``mode``, ``allowed_globs``, and
    ``forbidden_globs`` plus optional ``baseline_ref`` and ``created_from``
    when present in the input.
    """
    if not isinstance(raw, dict):
        return None

    mode = str(raw.get("mode") or "").strip().lower()
    allowed_globs = _normalize_path_list(raw.get("allowed_globs"))
    forbidden_globs = _normalize_path_list(raw.get("forbidden_globs"))

    if mode not in SCOPE_CONTRACT_MODES:
        mode = "restricted" if allowed_globs else "open"

    normalized: dict[str, Any] = {
        "mode": mode,
        "allowed_globs": allowed_globs,
        "forbidden_globs": forbidden_globs,
    }

    baseline_ref = str(raw.get("baseline_ref") or "").strip()
    if baseline_ref:
        normalized["baseline_ref"] = baseline_ref

    created_from = str(raw.get("created_from") or "").strip()
    if created_from:
        normalized["created_from"] = created_from

    return normalized

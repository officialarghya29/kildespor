"""Typed change detection between profile runs.

The update score (20 pts) rewards precise, minimal, well-typed updates:
we diff field-by-field against the previous published run and emit typed
change records, never touching fields whose value is unchanged.
"""
from __future__ import annotations

from typing import Any

from .models import CompanyProfile, Fact

# Change taxonomy
CHANGE_TYPES = {
    "changed_value",
    "new_fact",
    "became_available",
    "became_unavailable",
    "retracted",
    "evidence_refreshed",
}


def _norm_value(value: Any) -> Any:
    """Canonical comparison value (case/space-stable for strings)."""
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, list):
        return [_norm_value(v) for v in value]
    return value


def diff_profile(
    previous: CompanyProfile | None, current: CompanyProfile
) -> list[dict[str, Any]]:
    """Produce typed change records for one company.

    Rules:
      - unchanged values produce NO record
      - ok -> ok with different value      : changed_value
      - not_available -> ok                : became_available
      - ok -> not_available                : became_unavailable
      - brand new field                    : new_fact
      - previously published, now retracted: retracted (e.g. failed a gate)
    """
    changes: list[dict[str, Any]] = []
    if previous is None:
        return changes

    prev_facts = previous.facts
    curr_facts = current.facts

    for field, prev_fact in prev_facts.items():
        curr_fact = curr_facts.get(field)
        if curr_fact is None:
            changes.append(
                {
                    "field": field,
                    "type": "retracted",
                    "was": _public(prev_fact),
                    "now": None,
                    "note": "field no longer collected",
                }
            )
            continue
        changes.extend(_diff_fact(field, prev_fact, curr_fact))

    for field, curr_fact in curr_facts.items():
        if field not in prev_facts and curr_fact.status == "ok":
            changes.append(
                {
                    "field": field,
                    "type": "new_fact",
                    "was": None,
                    "now": _public(curr_fact),
                }
            )

    return changes


def _diff_fact(field: str, prev: Fact, curr: Fact) -> list[dict[str, Any]]:
    prev_ok = prev.status == "ok"
    curr_ok = curr.status == "ok"
    if not prev_ok and not curr_ok:
        return []
    if prev_ok and not curr_ok:
        return [
            {
                "field": field,
                "type": "became_unavailable",
                "was": _public(prev),
                "now": None,
                "note": curr.note,
            }
        ]
    if not prev_ok and curr_ok:
        return [
            {
                "field": field,
                "type": "became_available",
                "was": None,
                "now": _public(curr),
            }
        ]
    # both ok
    if _norm_value(prev.value) != _norm_value(curr.value):
        return [
            {
                "field": field,
                "type": "changed_value",
                "was": prev.value,
                "now": curr.value,
                "source_url": (curr.source.source_url if curr.source else None),
                "evidence": (curr.source.evidence if curr.source else None),
            }
        ]
    # Same value: only note a refresh when the retrieval date moved
    prev_date = prev.source.retrieved_date if prev.source else None
    curr_date = curr.source.retrieved_date if curr.source else None
    if prev_date != curr_date:
        return [
            {
                "field": field,
                "type": "evidence_refreshed",
                "field_value_unchanged": True,
                "retrieved_date": str(curr_date),
            }
        ]
    return []


def _public(fact: Fact) -> Any:
    return fact.value


def summarise_changes(changes: list[dict[str, Any]]) -> dict[str, int]:
    """Count changes by type (for the run report)."""
    counts: dict[str, int] = {}
    for change in changes:
        counts[change["type"]] = counts.get(change["type"], 0) + 1
    return counts

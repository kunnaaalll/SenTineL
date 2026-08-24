"""Compare agent (spec section 10.2) — deterministic fact alignment.

Pure, LLM-free alignment of ExtractedFacts into a comparison structure keyed
by (metric, period) with one cell per entity. Every gap is flagged, never
silently omitted:

- "missing" cells appear for entities with no fact for that row (while other
  entities do), value=None, status="missing".
- "conflict" marks rows where the same (entity, metric, period) carries
  different figures. Textually different but numerically identical values
  (e.g. "$391B" vs "$391,035 million") count as consistent.
- Mixed units across entities in a row ("million USD" vs "%") produce a row
  note and are counted — incomparable metrics stay visible instead of being
  dropped.

The graph's conditional edge invokes this agent only when the facts span
2+ entities or 2+ periods (comparison_warranted); invoked anyway with fewer,
it returns a warranted=False table rather than a misleading single-column
"comparison".
"""

from agents.state import AgentState, ExtractedFact


def normalize_metric(metric: str | None) -> str:
    return metric.strip().lower() if metric and metric.strip() else ""


def normalize_period(period: str | None) -> str:
    return period.strip().upper() if period and period.strip() else ""


def comparison_warranted(facts: list[ExtractedFact]) -> bool:
    """True when the facts span multiple entities or multiple periods."""
    entities = {fact.entity for fact in facts}
    periods = {normalize_period(fact.period) for fact in facts if normalize_period(fact.period)}
    return len(entities) >= 2 or len(periods) >= 2


def build_comparison_table(facts: list[ExtractedFact]) -> dict:
    if not comparison_warranted(facts):
        return {
            "warranted": False,
            "entities": sorted({fact.entity for fact in facts}),
            "rows": [],
            "notes": [
                "Comparison needs facts spanning at least two entities or two "
                "reporting periods; treating the evidence as a single summary."
            ],
        }

    all_entities = sorted({fact.entity for fact in facts})
    groups: dict[tuple[str, str], dict[str, list[ExtractedFact]]] = {}
    for fact in facts:
        metric = normalize_metric(fact.metric) or "unspecified metric"
        period = normalize_period(fact.period) or "unspecified period"
        groups.setdefault((metric, period), {}).setdefault(fact.entity, []).append(fact)

    rows: list[dict] = []
    conflict_rows = unit_mismatch_rows = missing_cells = 0

    for (metric, period), by_entity in sorted(groups.items()):
        cells: list[dict] = []
        units_in_row: set[str] = set()
        row_conflict = False

        for entity in all_entities:
            entity_facts = by_entity.get(entity)
            if not entity_facts:
                missing_cells += 1
                cells.append(
                    {
                        "entity": entity,
                        "value": None,
                        "unit": None,
                        "numeric_value": None,
                        "kind": None,
                        "status": "missing",
                        "source_chunk_ids": [],
                    }
                )
                continue

            textual = {(fact.value, fact.unit) for fact in entity_facts}
            numerics = {
                round(fact.numeric_value, 6)
                for fact in entity_facts
                if fact.numeric_value is not None
            }
            # Different text that parses to one number is consistent formatting,
            # not a conflict.
            conflict = len(textual) > 1 and len(numerics) != 1
            representative = max(entity_facts, key=lambda f: f.confidence)
            status = "conflict" if conflict else "ok"
            row_conflict = row_conflict or conflict
            chunk_ids = sorted({fact.source_chunk_id for fact in entity_facts})
            cells.append(
                {
                    "entity": entity,
                    "value": representative.value,
                    "unit": representative.unit,
                    "numeric_value": representative.numeric_value,
                    "kind": representative.kind,
                    "status": status,
                    "source_chunk_ids": chunk_ids,
                }
            )
            if representative.unit:
                units_in_row.add(representative.unit)

        note: str | None = None
        if row_conflict:
            conflict_rows += 1
            note = "conflicting reported values for the same entity/metric/period"
        if len(units_in_row) > 1:
            unit_mismatch_rows += 1
            mismatch_note = "units differ across entities (" + ", ".join(sorted(units_in_row)) + ")"
            note = f"{note}; {mismatch_note}" if note else mismatch_note
        rows.append({"metric": metric, "period": period, "cells": cells, "note": note})

    notes: list[str] = []
    if conflict_rows:
        notes.append(f"{conflict_rows} row(s) contain conflicting reported values")
    if missing_cells:
        notes.append(f"{missing_cells} cell(s) have no supporting evidence (flagged 'missing')")
    if unit_mismatch_rows:
        notes.append(
            f"{unit_mismatch_rows} row(s) mix units across entities; treat as indicative only"
        )

    return {"warranted": True, "entities": all_entities, "rows": rows, "notes": notes}


class CompareAgent:
    name = "compare"

    def __call__(self, state: AgentState) -> dict:
        facts = [ExtractedFact(**fact) for fact in state.get("extracted_facts", [])]
        table = build_comparison_table(facts)
        limitations = list(state.get("limitations", []))
        for note in table.get("notes", []):
            limitations.append(note)
        return {
            "comparison_table": table,
            "agent_path": [*state.get("agent_path", []), self.name],
            **({"limitations": limitations} if limitations else {}),
        }


__all__ = ["CompareAgent", "build_comparison_table", "comparison_warranted"]

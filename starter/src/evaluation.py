"""Evaluate retrieved evidence and preserve per-query traces.

A retrieved chunk is a hit when it comes from a gold source/page AND contains the
whole evidence_quote (after the same canonicalization on both sides). Human labels
for coherence and grounding are NOT inferred here; see the manual audit sheets.
"""
import re
import unicodedata
from collections import defaultdict
from typing import Any


def canon(text: str) -> str:
    """Canonical form for evidence matching: tolerant of PDF line wraps and case."""
    text = unicodedata.normalize("NFC", text).replace(" ", " ")
    text = re.sub(r"\bT (?=[a-z])", "T", text)
    text = re.sub(r"-\s+", "-", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def gold_pages(question: dict[str, Any]) -> set[int]:
    pages = question["gold_page"]
    return set(pages) if isinstance(pages, list) else {pages}


def contains_evidence(question: dict[str, Any], text: str) -> bool:
    quotes = question["evidence_quote"]
    quotes = quotes if isinstance(quotes, list) else [quotes]
    body = canon(text)
    return all(canon(q) in body for q in quotes)


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]],
                   strategy: str = "", filter_applied: dict[str, Any] | None = None,
                   evidence_in_some_chunk: bool | None = None) -> dict[str, Any]:
    """Label each top-k hit and compute hit@1 / Evidence hit@3 for one question."""
    pages = gold_pages(question)
    labeled = []
    for h in hits:
        on_gold = h["document_id"] == question["gold_source_id"] and h["page"] in pages
        has_ev = contains_evidence(question, h["text"])
        labeled.append({**h, "on_gold_page": on_gold, "contains_evidence": has_ev,
                        "is_hit": on_gold and has_ev})
    first = next((h["rank"] for h in labeled if h["is_hit"]), None)
    trace = {
        "strategy": strategy,
        "id": question["id"],
        "split": question["split"],
        "type": question.get("type"),
        "question": question["question"],
        "question_filter": question.get("filter"),
        "filter_applied": filter_applied,
        "gold_source_id": question["gold_source_id"],
        "gold_page": question["gold_page"],
        "evidence_quote": question["evidence_quote"],
        "expected_answer": question["expected_answer"],
        "hit_at_1": bool(labeled) and labeled[0]["is_hit"],
        "hit_at_3": any(h["is_hit"] for h in labeled[:3]),
        "first_hit_rank": first,
        "gold_page_in_top3": any(h["on_gold_page"] for h in labeled[:3]),
        "hits": labeled,
    }
    if question.get("filter"):
        trace["filter_compliant_top3"] = all(
            all(h.get(k) == v for k, v in question["filter"].items()) for h in labeled[:3])
    if evidence_in_some_chunk is not None:
        # False means the evidence is split across chunks: no single chunk can be a hit.
        trace["evidence_in_some_chunk"] = evidence_in_some_chunk
    return trace


def _ratio(n: int, d: int) -> dict[str, Any]:
    return {"n": n, "N": d, "rate": round(n / d, 3) if d else None}


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Hit@1 and Evidence Hit@3 by strategy and dev/test split."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in traces:
        groups[(t["strategy"], t["split"])].append(t)
    summary: dict[str, Any] = {}
    for (strategy, split), ts in sorted(groups.items()):
        filt = [t for t in ts if t.get("question_filter")]
        summary.setdefault(strategy, {})[split] = {
            "hit_at_1": _ratio(sum(t["hit_at_1"] for t in ts), len(ts)),
            "evidence_hit_at_3": _ratio(sum(t["hit_at_3"] for t in ts), len(ts)),
            "gold_page_in_top3": _ratio(sum(t["gold_page_in_top3"] for t in ts), len(ts)),
            "filter_questions_hit_at_3": _ratio(sum(t["hit_at_3"] for t in filt), len(filt)) if filt else "N/A",
            "filter_compliant_top3": _ratio(sum(t["filter_compliant_top3"] for t in filt), len(filt)) if filt else "N/A",
            "evidence_split_across_chunks": [t["id"] for t in ts if t.get("evidence_in_some_chunk") is False],
            "missed_ids": [t["id"] for t in ts if not t["hit_at_3"]],
        }
    return summary

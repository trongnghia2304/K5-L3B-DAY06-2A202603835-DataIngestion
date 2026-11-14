"""Day 06 pipeline: ingest -> chunk -> embed -> pgvector -> retrieve -> evaluate.

Run from starter/:
    python -m src.run                  # ingest + dev questions only
    python -m src.run --splits dev,test   # after the strategy choice is locked on dev

Outputs go to <repo>/results/.
"""
import argparse
import csv
import json
import os
import platform
import statistics
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import MyEmbedder
from .evaluation import contains_evidence, evaluate_query, summarize
from .ingestion import load_corpus
from .models import Chunk, DocumentPage
from .store import PgVectorStore

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results"

# The three strategies. B and C share identical chunks; C only adds the filter.
STRATEGIES: dict[str, dict[str, Any]] = {
    "A": {"chunking": "fixed_size", "size": 500, "overlap": 50, "use_filter": False},
    "B": {"chunking": "by_paragraph", "max_size": 500, "use_filter": False},
    "C": {"chunking": "by_paragraph", "max_size": 500, "use_filter": True},
}


def build_chunks(pages: list[DocumentPage], cfg: dict[str, Any]) -> list[Chunk]:
    if cfg["chunking"] == "fixed_size":
        return [c for p in pages for c in fixed_size(p, cfg["size"], cfg["overlap"])]
    return [c for p in pages for c in by_paragraph(p, cfg["max_size"])]


def chunk_stats(chunks: list[Chunk], embedder: MyEmbedder) -> dict[str, Any]:
    lengths = sorted(len(c.text) for c in chunks)
    tokens = [len(ids) for ids in embedder.model.tokenizer([c.text for c in chunks])["input_ids"]]
    q = statistics.quantiles(lengths, n=10)
    return {
        "chunk_count": len(chunks),
        "chars_total": sum(lengths),
        "chars_min": lengths[0], "chars_p10": round(q[0]), "chars_median": statistics.median(lengths),
        "chars_mean": round(statistics.mean(lengths), 1), "chars_p90": round(q[-1]), "chars_max": lengths[-1],
        "tokens_total": sum(tokens),
        "chunks_over_max_seq_length": sum(t > embedder.max_seq_length for t in tokens),
        "chunks_under_100_chars": sum(n < 100 for n in lengths),
    }


def ingest_strategy(store: PgVectorStore, embedder: MyEmbedder, strategy: str,
                    chunks: list[Chunk], precomputed: dict[str, list[float]] | None = None) -> dict[str, Any]:
    """Embed only new/changed chunks, upsert by stable ID, delete obsolete ones."""
    t0 = time.perf_counter()
    existing = store.existing_hashes(strategy)
    todo = [c for c in chunks
            if existing.get(c.chunk_id) != (c.metadata["content_hash"], embedder.model_name)]
    if precomputed is not None and all(c.chunk_id in precomputed for c in todo):
        vectors = [precomputed[c.chunk_id] for c in todo]
        embedded = 0
    else:
        vectors = embedder.embed_documents([c.text for c in todo])
        embedded = len(todo)
    t_embed = time.perf_counter() - t0
    written = store.upsert(strategy, todo, vectors, embedder.model_name) if todo else 0
    deleted = 0
    for doc_id in {c.document_id for c in chunks}:
        deleted += store.delete_obsolete(strategy, doc_id, {c.chunk_id for c in chunks if c.document_id == doc_id})
    return {"embedded": embedded, "rows_written": written, "rows_deleted_obsolete": deleted,
            "rows_in_store": store.count(strategy), "embed_seconds": round(t_embed, 2),
            "total_seconds": round(time.perf_counter() - t0, 2),
            "vectors": dict(zip((c.chunk_id for c in todo), vectors))}


def reingest_check(store: PgVectorStore, embedder: MyEmbedder, strategy: str,
                   chunks: list[Chunk]) -> dict[str, Any]:
    """Re-ingest the same version: nothing should be re-embedded, and forcing an
    upsert of existing rows must not change the row count."""
    before = store.count(strategy)
    second = ingest_strategy(store, embedder, strategy, chunks)
    sample = chunks[:20]
    store.upsert(strategy, sample, embedder.embed_documents([c.text for c in sample]), embedder.model_name)
    after = store.count(strategy)
    return {"rows_before": before, "rows_after": after, "re_embedded_on_second_pass": second["embedded"],
            "forced_upsert_of_existing_rows": len(sample), "duplicates_created": after - before}


def coherence_sheet(pages: list[DocumentPage], chunk_sets: dict[str, list[Chunk]]) -> list[dict[str, Any]]:
    """10 fixed positions chosen before scoring: midpoint of the first 10 counted pages."""
    rows = []
    for i, page in enumerate(sorted(pages, key=lambda p: p.page)[:10], 1):
        offset = len(page.text) // 2
        for strategy, chunks in chunk_sets.items():
            cover = [c for c in chunks if c.page == page.page
                     and c.metadata["char_start"] <= offset < c.metadata["char_end"]]
            for c in cover:
                rows.append({"position_id": f"pos{i:02d}", "page_id": page.metadata["page_id"],
                             "char_offset": offset, "strategy": strategy, "chunk_id": c.chunk_id,
                             "chunk_chars": len(c.text), "chunk_text": c.text,
                             "label_0_1_2": "", "reason": ""})
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def metrics_markdown(summary: dict[str, Any], report: dict[str, Any]) -> str:
    def fmt(r: Any) -> str:
        return r if isinstance(r, str) else f"{r['n']}/{r['N']}"
    lines = ["| Strategy | Split | Hit@1 | Evidence Hit@3 | Gold page in top-3 | Filter Qs Hit@3 | "
             "Filter compliant | Chunks | Ingest s | Cost |", "|---|---|---|---|---|---|---|---|---|---|"]
    for strategy, splits in summary.items():
        ing = report["strategies"][strategy]
        for split, m in splits.items():
            lines.append(f"| {strategy} | {split} | {fmt(m['hit_at_1'])} | {fmt(m['evidence_hit_at_3'])} | "
                         f"{fmt(m['gold_page_in_top3'])} | {fmt(m['filter_questions_hit_at_3'])} | "
                         f"{fmt(m['filter_compliant_top3'])} | {ing['stats']['chunk_count']} | "
                         f"{ing['ingest']['total_seconds']} | $0 |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--splits", default="dev", help="comma list: dev, test")
    parser.add_argument("--fresh", action="store_true",
                        help="delete stored chunks first so ingest time/cost is measured from scratch")
    args = parser.parse_args()
    splits = [s.strip() for s in args.splits.split(",") if s.strip()]

    load_dotenv(ROOT / "starter/.env")
    model_name = os.getenv("EMBEDDING_MODEL") or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. Load + normalize corpus; benchmark evidence must survive normalization.
    t0 = time.perf_counter()
    pages, corpus_report = load_corpus(ROOT)
    load_seconds = round(time.perf_counter() - t0, 2)
    questions = [json.loads(l) for l in (ROOT / "benchmark/questions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    page_text = {(p.document_id, p.page): p.text for p in pages}
    lost = [q["id"] for q in questions
            if not any(contains_evidence(q, page_text.get((q["gold_source_id"], pg), ""))
                       for pg in ([q["gold_page"]] if isinstance(q["gold_page"], int) else q["gold_page"]))]
    if lost:
        raise RuntimeError(f"Evidence no longer found on gold page after normalization: {lost}")

    # 2. Chunk.
    chunk_sets = {name: build_chunks(pages, cfg) for name, cfg in STRATEGIES.items()}

    # 3. Embed + store.
    t0 = time.perf_counter()
    embedder = MyEmbedder(model_name)
    model_load_seconds = round(time.perf_counter() - t0, 2)
    store = PgVectorStore(os.getenv("DATABASE_URL", ""), embedder.dimensions)
    store.create_schema()
    if args.fresh:
        for name in STRATEGIES:
            store.delete_strategy(name)
    with store._connect() as conn:
        pg_version, vec_version = conn.execute(
            "SELECT current_setting('server_version'), extversion FROM pg_extension WHERE extname='vector'").fetchone()

    report: dict[str, Any] = {
        "run_started_utc": started,
        "fresh_ingest": args.fresh,
        "splits": splits,
        "environment": {"python": platform.python_version(), "os": platform.platform(),
                        "postgresql": pg_version, "pgvector": vec_version,
                        **{pkg: version(pkg) for pkg in ("sentence-transformers", "torch", "psycopg", "pypdf")}},
        "embedding": {"provider": "local sentence-transformers", "model": embedder.model_name,
                      "dimensions": embedder.dimensions, "max_seq_length": embedder.max_seq_length,
                      "normalized": True, "query_document_handling": "same encoder, no prefixes",
                      "model_load_seconds": model_load_seconds, "cost_usd": 0},
        "vector_store": {"backend": "PostgreSQL + pgvector", "table": store.table, "metric": "cosine (<=>)",
                         "search": "exact (sequential scan, no ANN index)"},
        "corpus": {**corpus_report, "pages_loaded": len(pages), "load_normalize_seconds": load_seconds},
        "strategies": {},
    }
    vectors_b: dict[str, list[float]] | None = None
    for name, cfg in STRATEGIES.items():
        chunks = chunk_sets[name]
        # C stores exactly B's chunks; reuse B's vectors instead of re-embedding the same text.
        pre = vectors_b if name == "C" else None
        ing = ingest_strategy(store, embedder, name, chunks, pre)
        if name == "B":
            vectors_b = ing["vectors"]
        ing.pop("vectors")
        report["strategies"][name] = {"config": cfg, "stats": chunk_stats(chunks, embedder), "ingest": ing,
                                      "reingest_check": reingest_check(store, embedder, name, chunks)}
        print(f"[{name}] {report['strategies'][name]['stats']['chunk_count']} chunks, ingest {ing}")

    # 4. Retrieve + evaluate.
    selected = [q for q in questions if q["split"] in splits]
    t0 = time.perf_counter()
    qvecs = {q["id"]: embedder.embed_query(q["question"]) for q in selected}
    query_embed_seconds = round(time.perf_counter() - t0, 2)
    traces = []
    for name, cfg in STRATEGIES.items():
        by_page: dict[tuple[str, int], list[Chunk]] = {}
        for c in chunk_sets[name]:
            by_page.setdefault((c.document_id, c.page), []).append(c)
        for q in selected:
            filt = q["filter"] if cfg["use_filter"] and q.get("filter") else None
            hits = store.search(name, qvecs[q["id"]], top_k=3, filters=filt)
            gp = [q["gold_page"]] if isinstance(q["gold_page"], int) else q["gold_page"]
            in_some = any(contains_evidence(q, c.text) for pg in gp
                          for c in by_page.get((q["gold_source_id"], pg), []))
            traces.append(evaluate_query(q, hits, name, filt, in_some))
    summary = summarize(traces)
    report["retrieval"] = {"splits": splits, "top_k": 3, "query_embed_seconds": query_embed_seconds}

    # 5. Write artifacts.
    tag = "_".join(splits)
    for split in splits:
        for name in STRATEGIES:
            write_jsonl(RESULTS / "traces" / f"{split}_{name}.jsonl",
                        [t for t in traces if t["split"] == split and t["strategy"] == name])
    (RESULTS / f"metrics_{tag}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (RESULTS / f"metrics_{tag}.md").write_text(metrics_markdown(summary, report), encoding="utf-8")
    (RESULTS / "ingest_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (RESULTS / "run_history.jsonl").open("a", encoding="utf-8") as f:  # every run, never overwritten
        f.write(json.dumps({"run_started_utc": started, "fresh_ingest": args.fresh, "splits": splits,
                            "ingest": {k: v["ingest"] for k, v in report["strategies"].items()},
                            "summary": summary}, ensure_ascii=False) + "\n")
    for name, chunks in chunk_sets.items():
        write_jsonl(RESULTS / "chunks" / f"{name}.jsonl",
                    [{"chunk_id": c.chunk_id, "page": c.page, "chars": len(c.text), "metadata": c.metadata,
                      "text": c.text} for c in chunks])
    manual = RESULTS / "manual"
    if not (manual / "coherence_labels.csv").exists():  # never overwrite human labels
        write_csv(manual / "coherence_labels.csv", coherence_sheet(pages, chunk_sets))
    if "test" in splits:
        test_traces = [t for t in traces if t["split"] == "test"]
        if not (manual / "metadata_audit.csv").exists():
            write_csv(manual / "metadata_audit.csv", [
                {"question_id": t["id"], "strategy": t["strategy"], "rank": h["rank"], "chunk_id": h["chunk_id"],
                 "source": h["source"], "page": h["page"], "section": h["section"], "region": h["region"],
                 "question_filter": json.dumps(t["question_filter"]) if t["question_filter"] else "",
                 "chunk_text": h["text"], "metadata_complete_and_correct_y_n": "", "notes": ""}
                for t in test_traces for h in t["hits"]])
        if not (manual / "grounding_labels.csv").exists():
            write_csv(manual / "grounding_labels.csv", [
                {"question_id": t["id"], "strategy": t["strategy"], "question": t["question"],
                 "expected_answer": t["expected_answer"], "auto_evidence_hit_at_3": t["hit_at_3"],
                 "top3_pages": " | ".join(str(h["page"]) for h in t["hits"]),
                 "top3_text": "\n---\n".join(h["text"] for h in t["hits"]),
                 "label_0_1_2": "", "reason": ""} for t in test_traces])
    print((RESULTS / f"metrics_{tag}.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

"""Verify benchmark/questions.jsonl against the source PDF before any retrieval run.

Checks: required fields, unique IDs, split sizes, filter counts, that every
evidence_quote occurs on its gold page after normalization, and that filter
values exist in data/section_map.json and match the gold page.

Run:  python starter/scripts/verify_benchmark.py
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

import pypdf

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = ["id", "split", "question", "expected_answer", "gold_source_id",
            "gold_page", "evidence_quote", "filter"]


def normalize(text: str) -> str:
    """Same baseline normalization as the manifest: NFC + collapsed whitespace."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def main() -> None:
    manifest = json.loads((ROOT / "data/manifest.json").read_text(encoding="utf-8"))
    section_map = json.loads((ROOT / "data/section_map.json").read_text(encoding="utf-8"))
    sources = {s["source_id"]: s for s in manifest["sources"]}

    texts: dict[str, dict[int, str]] = {}
    for sid, src in sources.items():
        reader = pypdf.PdfReader(ROOT / src["source_path_or_url"])
        texts[sid] = {i: normalize(p.extract_text() or "") for i, p in enumerate(reader.pages, 1)}

    errors: list[str] = []
    region_pages = {}
    for r in section_map["regions"]:
        region_pages[r["region"]] = set(r["pdf_pages"])
        for page in r["pdf_pages"]:
            if r["heading"] not in texts[section_map["source_id"]][page].upper():
                errors.append(f"section_map: heading '{r['heading']}' not found on page {page}")

    questions = [json.loads(line) for line in
                 (ROOT / "benchmark/questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [q.get("id") for q in questions]
    if len(ids) != len(set(ids)):
        errors.append("duplicate question ids")

    for q in questions:
        missing = [k for k in REQUIRED if k not in q]
        if missing:
            errors.append(f"{q.get('id')}: missing fields {missing}")
            continue
        pages = q["gold_page"] if isinstance(q["gold_page"], list) else [q["gold_page"]]
        page_texts = texts.get(q["gold_source_id"], {})
        quote = normalize(q["evidence_quote"])
        if not any(quote in page_texts.get(p, "") for p in pages):
            errors.append(f"{q['id']}: evidence_quote not found on page(s) {pages}")
        counted = {p["pdf_page"] for p in sources[q["gold_source_id"]]["pages"] if p["counted"]}
        if not set(pages) & counted:
            errors.append(f"{q['id']}: gold page(s) {pages} not among counted content pages")
        if q["filter"]:
            for key, value in q["filter"].items():
                if key != "region" or value not in region_pages:
                    errors.append(f"{q['id']}: unknown filter {key}={value}")
                elif not set(pages) & region_pages[value]:
                    errors.append(f"{q['id']}: gold page(s) {pages} outside filter region {value}")

    dev = [q for q in questions if q.get("split") == "dev"]
    test = [q for q in questions if q.get("split") == "test"]
    filt = [q for q in questions if q.get("filter")]
    filt_test = [q for q in test if q.get("filter")]
    print(f"questions={len(questions)} dev={len(dev)} test={len(test)} "
          f"with_filter={len(filt)} (test: {len(filt_test)})")
    if len(questions) < 20 or len(dev) < 15 or len(test) < 5:
        errors.append("need >=20 questions with >=15 dev and >=5 test")
    if len(filt) < 3 or not filt_test:
        errors.append("need >=3 filter questions with >=1 in test")

    if errors:
        print("FAILED:")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print("All evidence quotes and filters verified.")


if __name__ == "__main__":
    main()

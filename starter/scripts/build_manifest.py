"""Build data/manifest.json for the corpus: page counts, exclusions and character counts.

Run from the repo root or starter/:  python starter/scripts/build_manifest.py
Character counts use raw pypdf text with whitespace collapsed; the pipeline's
normalization step may produce slightly different counts.
"""
import hashlib
import json
import re
import unicodedata
from pathlib import Path

import pypdf

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

SOURCES = [
    {
        "source_id": "wb-ar-2024",
        "title": "The World Bank Annual Report 2024: A Better Bank for a Better World",
        "file": "worldbank-report-2024.pdf",
        "source_path_or_url": "data/worldbank-report-2024.pdf",
        "doi": "10.1596/AR2024EN",
        "publisher": "International Bank for Reconstruction and Development / The World Bank",
        "license_or_permission": (
            "CC BY-NC-ND 3.0 IGO (stated on PDF page 48, 'Rights and Permissions'). "
            "Used unmodified for non-commercial coursework with attribution; "
            "third-party components (photos, some tables/figures) may need separate permission."
        ),
        "accessed_at": "2026-11-14",
        "version_note": "PDF metadata CreationDate 2024-10-08, ModDate 2024-10-18",
        # PDF pages (1-based) excluded from the content count, with the reason.
        "excluded_pages": {
            1: "front cover",
            2: "table of contents / report scope boilerplate",
            9: "section divider (Results by Region), title only",
            24: "section divider (Results by Theme), title only",
            36: "section divider (How We Work), title only",
            48: "photo credits and copyright/rights notice",
            49: "back cover",
        },
    }
]

MIN_CONTENT_CHARS = 500  # sanity floor: a counted page must carry real text


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def main() -> None:
    manifest = {
        "counting_rule": (
            "One unit = one physical PDF page. Each PDF page of this report is a two-page "
            "printed spread, so a PDF page carries two printed pages. Cover, contents, "
            "section dividers, credits and back cover are excluded. A counted page must "
            f"have at least {MIN_CONTENT_CHARS} extracted characters."
        ),
        "extraction_tool": f"pypdf {pypdf.__version__}",
        "sources": [],
    }
    total_counted = 0
    for src in SOURCES:
        path = DATA / src["file"]
        reader = pypdf.PdfReader(path)
        pages = []
        for number, page in enumerate(reader.pages, 1):
            text = collapse(page.extract_text() or "")
            reason = src["excluded_pages"].get(number)
            if reason is None and len(text) < MIN_CONTENT_CHARS:
                reason = f"too little text ({len(text)} chars)"
            pages.append({
                "page_id": f"{src['source_id']}-p{number:03d}",
                "pdf_page": number,
                "chars": len(text),
                "counted": reason is None,
                "excluded_reason": reason,
            })
        counted = [p for p in pages if p["counted"]]
        total_counted += len(counted)
        entry = {k: v for k, v in src.items() if k not in ("file", "excluded_pages")}
        entry.update({
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "physical_pdf_pages": len(pages),
            "counted_content_pages": len(counted),
            "counted_chars": sum(p["chars"] for p in counted),
            "pages": pages,
        })
        manifest["sources"].append(entry)
    manifest["total_counted_content_pages"] = total_counted
    out = DATA / "manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out.relative_to(ROOT)}: {total_counted} counted content pages")
    if total_counted < 30:
        raise SystemExit("Corpus has fewer than 30 content pages.")


if __name__ == "__main__":
    main()

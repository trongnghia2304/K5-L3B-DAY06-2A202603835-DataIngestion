"""Read and normalize source data, keeping every page traceable to its source."""
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import pypdf

from .models import DocumentPage

# Running header of this report, plus the printed page numbers glued around it
# (e.g. "2120\nTHE WORLD BANK ANNUAL REPORT 2024 THE WORLD BANK ANNUAL REPORT 2024 21").
HEADER_RE = re.compile(r"(?:\b\d{1,4}[ \t]*\n?[ \t]*)?(?:THE WORLD BANK ANNUAL REPORT 2024[ \t]*)+(?:\d{1,4}\b)?")
# Kerning artifact from the PDF: "T o", "T otal", "T anzania", "Mid-T erm".
KERNING_T_RE = re.compile(r"\bT (?=[a-z])")
# Line-wrapped hyphenated compounds: "principal-\nprotected" -> "principal-protected".
WRAPPED_HYPHEN_RE = re.compile(r"-[ \t]*\n[ \t]*")


def load_pdf(path: Path, document_id: str, source: str,
             pages: list[int] | None = None) -> tuple[list[DocumentPage], list[dict[str, Any]]]:
    """Extract each physical page with its page number and source.

    Returns (pages, errors). Pages that fail to extract or come out empty are
    recorded in errors instead of being dropped silently. OCR is not attempted.
    """
    reader = pypdf.PdfReader(path)
    wanted = set(pages) if pages else None
    result: list[DocumentPage] = []
    errors: list[dict[str, Any]] = []
    for number, page in enumerate(reader.pages, 1):
        if wanted is not None and number not in wanted:
            continue
        try:
            raw = page.extract_text() or ""
        except Exception as exc:  # pypdf raises several error types per page
            errors.append({"document_id": document_id, "page": number, "error": f"extract failed: {exc!r}"})
            continue
        if not raw.strip():
            errors.append({"document_id": document_id, "page": number, "error": "no extractable text (OCR not run)"})
            continue
        result.append(DocumentPage(document_id=document_id, source=source, page=number, text=raw))
    return result, errors


def normalize_text(text: str, stats: dict[str, int] | None = None) -> str:
    """Normalize Unicode and whitespace while keeping line structure for chunking."""
    def count(name: str, n: int) -> None:
        if stats is not None:
            stats[name] = stats.get(name, 0) + n

    text = unicodedata.normalize("NFC", text).replace(" ", " ").replace("\r", "")
    text, n = HEADER_RE.subn("\n", text)
    count("running_headers_removed", n)
    text, n = KERNING_T_RE.subn("T", text)
    count("kerning_fixes", n)
    text, n = WRAPPED_HYPHEN_RE.subn("-", text)
    count("wrapped_hyphens_joined", n)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def load_corpus(root: Path) -> tuple[list[DocumentPage], dict[str, Any]]:
    """Load the counted content pages listed in data/manifest.json with metadata."""
    manifest = json.loads((root / "data/manifest.json").read_text(encoding="utf-8"))
    section_map = json.loads((root / "data/section_map.json").read_text(encoding="utf-8"))
    section_of = {p: s["section"] for s in section_map["sections"] for p in s["pdf_pages"]}
    region_of = {p: r["region"] for r in section_map["regions"] for p in r["pdf_pages"]}

    pages: list[DocumentPage] = []
    report: dict[str, Any] = {"errors": [], "normalization": {}, "sources": []}
    for src in manifest["sources"]:
        counted = [p["pdf_page"] for p in src["pages"] if p["counted"]]
        loaded, errors = load_pdf(root / src["source_path_or_url"], src["source_id"],
                                  src["source_path_or_url"], counted)
        report["errors"].extend(errors)
        for page in loaded:
            page.text = normalize_text(page.text, report["normalization"])
            if src["source_id"] == section_map["source_id"]:
                page.metadata["section"] = section_of.get(page.page)
                page.metadata["region"] = region_of.get(page.page)
            page.metadata["page_id"] = f"{src['source_id']}-p{page.page:03d}"
        pages.extend(loaded)
        report["sources"].append({"source_id": src["source_id"], "expected_pages": len(counted),
                                  "loaded_pages": len(loaded)})
    if len(pages) < 30:
        raise RuntimeError(f"Only {len(pages)} content pages loaded; the lab requires at least 30.")
    return pages, report

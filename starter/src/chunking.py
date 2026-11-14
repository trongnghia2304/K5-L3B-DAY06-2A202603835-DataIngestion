"""Fixed-size and structure-aware chunking. Chunks never cross a page boundary,
so every chunk keeps an exact source page; char offsets point into page.text."""
import hashlib
import re

from .models import Chunk, DocumentPage

SENTENCE_END = re.compile(r"[.!?:;][\"'’”)]*$")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9$“\"(])")


def _make_chunk(page: DocumentPage, index: int, start: int, end: int, method: str) -> Chunk:
    text = page.text[start:end].strip()
    chunk_id = f"{page.document_id}:p{page.page:03d}:{method}:{index:03d}"
    metadata = dict(page.metadata)
    metadata.update({
        "char_start": start,
        "char_end": end,
        "chunk_method": method,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    })
    return Chunk(chunk_id=chunk_id, document_id=page.document_id, source=page.source,
                 page=page.page, text=text, metadata=metadata)


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Split text into overlapping character windows, ignoring structure."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    chunks: list[Chunk] = []
    step = size - overlap
    start = 0
    while start < len(page.text):
        end = min(start + size, len(page.text))
        chunks.append(_make_chunk(page, len(chunks), start, end, f"fixed{size}o{overlap}"))
        if end == len(page.text):
            break
        start += step
    return chunks


def _is_heading(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return (len(line) <= 80 and bool(letters) and not SENTENCE_END.search(line)
            and (sum(c.isupper() for c in letters) / len(letters) > 0.6))


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Group lines into paragraphs: a paragraph ends after a line with terminal
    punctuation; an all-caps heading line starts a new paragraph."""
    spans: list[tuple[int, int]] = []
    start = None
    pos = 0
    for line in text.split("\n"):
        line_start, line_end = pos, pos + len(line)
        pos = line_end + 1
        if _is_heading(line) and start is not None:
            spans.append((start, line_start - 1))
            start = None
        if start is None:
            start = line_start
        if SENTENCE_END.search(line):
            spans.append((start, line_end))
            start = None
    if start is not None:
        spans.append((start, len(text)))
    return spans


def _split_oversized(text: str, start: int, end: int, max_size: int) -> list[tuple[int, int]]:
    """Split one long paragraph at sentence boundaries; hard-cut only if a single
    sentence exceeds max_size."""
    pieces: list[tuple[int, int]] = []
    sentence_starts = [start] + [start + m.end() for m in SENTENCE_SPLIT.finditer(text[start:end])]
    bounds = sentence_starts + [end]
    cur_start = start
    for i in range(1, len(bounds)):
        if bounds[i] - cur_start > max_size and bounds[i - 1] > cur_start:
            pieces.append((cur_start, bounds[i - 1]))
            cur_start = bounds[i - 1]
    pieces.append((cur_start, end))
    result: list[tuple[int, int]] = []
    for s, e in pieces:
        while e - s > max_size:
            cut = text.rfind(" ", s, s + max_size)
            cut = cut if cut > s else s + max_size
            result.append((s, cut))
            s = cut
        result.append((s, e))
    return result


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Group consecutive paragraphs up to max_size chars; split oversized ones."""
    units: list[tuple[int, int]] = []
    for s, e in _paragraph_spans(page.text):
        units.extend(_split_oversized(page.text, s, e, max_size) if e - s > max_size else [(s, e)])
    chunks: list[Chunk] = []
    cur: tuple[int, int] | None = None
    for s, e in units:
        if cur is None:
            cur = (s, e)
        elif e - cur[0] <= max_size:
            cur = (cur[0], e)
        else:
            chunks.append(_make_chunk(page, len(chunks), cur[0], cur[1], f"para{max_size}"))
            cur = (s, e)
    if cur is not None:
        chunks.append(_make_chunk(page, len(chunks), cur[0], cur[1], f"para{max_size}"))
    return [c for c in chunks if c.text]

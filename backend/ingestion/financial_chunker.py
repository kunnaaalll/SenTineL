"""Financial-document chunker (spec section 7, step 2).

Three content shapes, three behaviors:

- Prose (MD&A, business overview, ...) -> sentence-boundary splitting,
  ~TARGET_CHARS chunks with ~OVERLAP_CHARS of overlap.
- Tables (markdown pipe-table blocks, as produced by
  data_sources.sec_edgar.html_to_financial_text) -> one atomic chunk per
  table, never split mid-table.
- Footnotes (paragraphs starting "(1)"-style markers) -> attached to the
  preceding chunk's metadata["footnotes"]; never embedded standalone. If a
  footnote appears before any content chunk, it attaches to the first chunk
  that gets created.

`Item N.` headings update the `section` label on subsequently-created chunks
(e.g. "Item 7 - Management's Discussion and Analysis", "Item 1A - Risk
Factors").

Chunk ids are deterministic (sha256 of source_id|section|ordinal) so
re-ingesting an unchanged document upserts over the same vectors instead of
duplicating them.
"""

import hashlib
import re
from dataclasses import dataclass

from models.schemas import Chunk, RawDocument

TARGET_CHARS = 800  # spec section 7: ~800 char prose chunks
OVERLAP_CHARS = 150  # spec section 7: 150 char overlap

_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_ITEM_HEADING = re.compile(r"^\s*[Ii]tem\s+(\d{1,2}[A-B]?)\s*[.:—–-]\s*(.*)$")
# "(1) ..." / "1. Sentence..." — deliberately conservative to avoid eating
# numbered body text; continuation paragraphs starting lowercase are absorbed.
_FOOTNOTE_START = re.compile(r"^(?:\(\d{1,2}\)\s+|\d{1,2}\.\s+[A-Z])")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")


@dataclass
class _Block:
    kind: str  # "prose" | "table" | "heading"
    text: str
    start: int  # char span in the original document
    end: int


# --------------------------------------------------------------------------
# Structural pass: raw text -> ordered blocks
# --------------------------------------------------------------------------


def split_blocks(text: str) -> list[_Block]:
    """Split raw text into table / heading / prose blocks.

    - Consecutive markdown-table lines form one table block (blank lines do
      not break a table; any non-table line does).
    - Blank-line-separated non-table runs become one prose block each,
      whitespace-normalized.
    - Short 'Item N.' lines become heading blocks and split surrounding prose.
    """
    blocks: list[_Block] = []
    para_buf: list[str] = []
    para_start: int | None = None
    table_buf: list[str] = []
    table_start: int | None = None

    def flush_para(end_pos: int) -> None:
        nonlocal para_buf, para_start
        if para_buf:
            joined = re.sub(r"\s+", " ", " ".join(line.strip() for line in para_buf)).strip()
            if joined:
                blocks.append(_Block("prose", joined, para_start or 0, end_pos))
            para_buf, para_start = [], None

    def flush_table(end_pos: int) -> None:
        nonlocal table_buf, table_start
        if table_buf:
            blocks.append(_Block("table", "\n".join(table_buf), table_start or 0, end_pos))
            table_buf, table_start = [], None

    pos = 0
    for line in text.splitlines():
        line_end = pos + len(line)
        stripped = line.strip()
        if _TABLE_LINE.match(line):
            flush_para(pos)
            if table_start is None:
                table_start = pos
            table_buf.append(line.rstrip())
        elif not stripped:
            flush_para(pos)  # blank line closes a paragraph, not a table
        else:
            heading = _ITEM_HEADING.match(stripped) if len(stripped) <= 120 else None
            if heading:
                flush_para(pos)
                flush_table(pos)
                label = f"Item {heading.group(1)}"
                rest = heading.group(2).strip().rstrip(".")
                if rest:
                    label += f" - {rest}"
                blocks.append(_Block("heading", label, pos, line_end))
            else:
                flush_table(pos)
                if para_start is None:
                    para_start = pos
                para_buf.append(line)
        pos = line_end + 1  # account for the newline
    flush_para(pos)
    flush_table(pos)
    return blocks


# --------------------------------------------------------------------------
# Sentence splitting / packing
# --------------------------------------------------------------------------


def split_sentences(text: str) -> list[str]:
    return [s for s in (part.strip() for part in _SENTENCE_BOUNDARY.split(text)) if s]


def pack_sentences(
    sentences: list[str], target_chars: int = TARGET_CHARS, overlap_chars: int = OVERLAP_CHARS
) -> list[str]:
    """Greedily pack sentences into ~target_chars pieces.

    The tail sentences of each piece (up to overlap_chars, always at least one
    full sentence) are carried into the next piece, so consecutive chunks
    share their boundary content. Sentences longer than target_chars are
    hard-wrapped with the same overlap — sentence splitting is best-effort on
    financial text (abbreviations like 'U.S.' defeat pure regex splitting).
    """
    units: list[str] = []
    step = max(target_chars - overlap_chars, 1)
    for sentence in sentences:
        if len(sentence) <= target_chars:
            units.append(sentence)
        else:
            for i in range(0, len(sentence), step):
                units.append(sentence[i : i + target_chars])

    pieces: list[str] = []
    cur: list[str] = []
    cur_len = 0

    def emit(cur_sentences: list[str]) -> None:
        piece = " ".join(cur_sentences)
        if not pieces or pieces[-1] != piece:
            pieces.append(piece)

    for unit in units:
        if cur and cur_len + len(unit) + 1 > target_chars:
            emit(cur)
            tail = _overlap_tail(cur, overlap_chars)
            cur, cur_len = list(tail), sum(len(t) + 1 for t in tail)
        cur.append(unit)
        cur_len += len(unit) + 1
    if cur:
        emit(cur)
    return pieces


def _overlap_tail(sentences: list[str], overlap_chars: int) -> list[str]:
    """Trailing sentences totaling <= overlap_chars; always at least one."""
    tail: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        if tail and total + len(sentence) + 1 > overlap_chars:
            break
        tail.insert(0, sentence)
        total += len(sentence) + 1
    return tail


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def chunk_document(
    doc: RawDocument, target_chars: int = TARGET_CHARS, overlap_chars: int = OVERLAP_CHARS
) -> list[Chunk]:
    """RawDocument -> list[Chunk] per spec section 7 step 2.

    entities stays empty here; entity_extractor (later phase) fills it.
    doc.metadata flows onto every chunk so the vector store can index
    filterable fields (ticker, filing date, title).
    """
    chunks: list[Chunk] = []
    pending_footnotes: list[str] = []  # footnotes seen before any chunk exists
    ordinal = 0
    section: str | None = None
    last_was_footnote = False

    def new_chunk(text: str, page_or_position: str) -> Chunk:
        nonlocal ordinal
        metadata = dict(doc.metadata)
        metadata.setdefault("title", doc.title)
        if doc.published_date:
            metadata.setdefault("published_date", doc.published_date.isoformat())
        if pending_footnotes:
            # Set BEFORE Chunk(...) — pydantic v2 copies dicts during
            # validation, so post-construction mutation would be lost.
            metadata["footnotes"] = list(pending_footnotes)
            pending_footnotes.clear()
        chunk = Chunk(
            chunk_id=_chunk_id(doc.source_id, section, ordinal),
            source_id=doc.source_id,
            source_type=doc.source_type,
            section=section,
            page_or_position=page_or_position,
            text=text,
            entities=[],  # entity_extractor arrives in a later phase
            metadata=metadata,
        )
        ordinal += 1
        chunks.append(chunk)
        return chunk

    for block in split_blocks(doc.raw_text):
        if block.kind == "heading":
            section = block.text
            last_was_footnote = False
            continue

        if block.kind == "table":
            # Atomic: one chunk per table regardless of length.
            new_chunk(block.text, f"table chars {block.start}-{block.end}")
            last_was_footnote = False
            continue

        # Prose paragraph: footnote detection first.
        is_footnote = bool(_FOOTNOTE_START.match(block.text)) or (
            last_was_footnote and block.text[:1].islower()
        )
        if is_footnote:
            if chunks:
                chunks[-1].metadata.setdefault("footnotes", []).append(block.text)
            else:
                pending_footnotes.append(block.text)
            last_was_footnote = True
            continue
        last_was_footnote = False

        pieces = pack_sentences(split_sentences(block.text), target_chars, overlap_chars)
        for i, piece in enumerate(pieces):
            page = f"chars {block.start}-{block.end}"
            if len(pieces) > 1:
                page += f" part {i + 1}/{len(pieces)}"
            new_chunk(piece, page)

    return chunks


def _chunk_id(source_id: str, section: str | None, ordinal: int) -> str:
    key = f"{source_id}|{section}|{ordinal}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

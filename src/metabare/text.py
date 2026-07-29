"""Text normalisation and chunking.

Normalise and chunk only when required, so the default here is to leave a note
as a single chunk and only split when it exceeds what the embedding model can
actually read. That threshold is a property of the
model (BGE truncates at 512 tokens), not a style choice: text past the limit is
silently dropped by the tokenizer, so a long note that is not chunked is a note
whose ending is not searchable.

Splitting prefers paragraph boundaries, then line boundaries, then a hard cut.
Terminal output and log excerpts are a large part of this corpus and their line
structure carries meaning, so enough of it is preserved to keep search and
debugging useful: lines are not reflowed, and internal whitespace within a
line is left alone.
"""

from __future__ import annotations

import re
import unicodedata

# Roughly four characters per token for English technical text, against a 512
# token limit, leaving headroom for the query prefix and special tokens. Chosen
# as a conservative character budget rather than tokenising twice; a chunk
# slightly under the limit costs nothing, one over loses text silently.
DEFAULT_MAX_CHARS = 1600
DEFAULT_OVERLAP_CHARS = 160

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)


def normalise(text: str) -> str:
    """Normalise text for indexing without destroying its structure.

    NFC composition and CRLF folding only. Case, punctuation and internal
    spacing are left alone: BM25 tokenisation handles case, and collapsing
    whitespace would ruin the layout of a stack trace or a terminal capture.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", text)
    text = _TRAILING_WS.sub("", text)
    return text.strip("\n")


def derive_title(text: str, *, fallback: str = "", max_length: int = 120) -> str:
    """Pick a display title from note content.

    A leading Markdown heading wins, otherwise the first non-empty line.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        if not stripped:
            continue
        return stripped[:max_length]
    return fallback[:max_length]


def _split_oversized(block: str, max_chars: int, overlap: int) -> list[str]:
    """Split a single block that is too large, preferring line boundaries."""
    chunks: list[str] = []
    remaining = block
    while len(remaining) > max_chars:
        window = remaining[:max_chars]
        cut = window.rfind("\n")
        # Only honour a line break in the last third of the window; a break
        # near the start would produce a chunk mostly made of nothing.
        if cut < max_chars // 3:
            cut = max_chars
        chunks.append(remaining[:cut].strip("\n"))
        step = max(cut - overlap, 1)
        # Snap the overlap back to a line boundary. Starting the next chunk
        # mid-line would prefix it with the tail of a log record or a command,
        # which reads as noise to BM25 and to a human skimming the excerpt.
        newline = remaining.find("\n", step - 1, cut)
        if newline != -1:
            step = newline + 1
        remaining = remaining[step:]
    if remaining.strip():
        chunks.append(remaining.strip("\n"))
    return chunks


def chunk(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Split normalised text into chunks that fit the embedding model.

    Returns a single-element list for anything that already fits, which is the
    common case for a note. Never returns an empty list for non-empty input,
    and never returns an empty chunk.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not 0 <= overlap_chars < max_chars:
        raise ValueError("overlap_chars must be non-negative and smaller than max_chars")

    text = normalise(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buffer = ""
    for block in _PARAGRAPH_BREAK.split(text):
        block = block.strip("\n")
        if not block:
            continue
        if len(block) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(_split_oversized(block, max_chars, overlap_chars))
            continue
        candidate = f"{buffer}\n\n{block}" if buffer else block
        if len(candidate) <= max_chars:
            buffer = candidate
        else:
            chunks.append(buffer)
            buffer = block
    if buffer:
        chunks.append(buffer)
    return [c for c in chunks if c.strip()]


def excerpt(text: str, *, max_length: int = 280) -> str:
    """Short preview for a result card, cut on a word boundary where possible."""
    text = " ".join(normalise(text).split())
    if len(text) <= max_length:
        return text
    cut = text.rfind(" ", 0, max_length - 1)
    if cut < max_length // 2:
        cut = max_length - 1
    return text[:cut].rstrip() + "…"

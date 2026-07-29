"""Normalisation and chunking."""

from __future__ import annotations

import pytest

from metabare.text import (
    DEFAULT_MAX_CHARS,
    chunk,
    derive_title,
    excerpt,
    normalise,
)


class TestNormalise:
    def test_folds_line_endings(self) -> None:
        assert normalise("a\r\nb\rc") == "a\nb\nc"

    def test_preserves_internal_structure(self) -> None:
        """Terminal captures lose their meaning if reflowed."""
        text = "$ kubectl get pods\nNAME      READY\napi-0     1/1"
        assert normalise(text) == text

    def test_strips_trailing_whitespace_per_line(self) -> None:
        assert normalise("a   \nb\t\n") == "a\nb"

    def test_composes_unicode(self) -> None:
        assert normalise("é") == "é"


class TestChunk:
    def test_short_text_is_one_chunk(self) -> None:
        assert chunk("a short note") == ["a short note"]

    def test_empty_input_yields_nothing(self) -> None:
        assert chunk("") == []
        assert chunk("   \n\n  ") == []

    def test_splits_on_paragraphs_when_oversized(self) -> None:
        para = "x" * (DEFAULT_MAX_CHARS // 2)
        chunks = chunk(f"{para}\n\n{para}\n\n{para}")
        assert len(chunks) > 1
        assert all(len(c) <= DEFAULT_MAX_CHARS for c in chunks)

    def test_splits_a_single_oversized_block(self) -> None:
        chunks = chunk("y" * (DEFAULT_MAX_CHARS * 3))
        assert len(chunks) >= 3
        assert all(len(c) <= DEFAULT_MAX_CHARS for c in chunks)

    def test_never_emits_empty_chunks(self) -> None:
        chunks = chunk("a\n\n\n\nb\n\n\n\n" + "z" * (DEFAULT_MAX_CHARS * 2))
        assert all(c.strip() for c in chunks)

    def test_prefers_line_boundaries_in_a_long_block(self) -> None:
        line = "2026-07-29T10:00:00Z ERROR failed to delete resource\n"
        chunks = chunk(line * 200)
        # A cut that lands mid-line leaves a fragment that starts with a
        # partial timestamp; every chunk should start at a record boundary.
        assert all(c.startswith("2026-") for c in chunks)

    def test_overlap_must_be_smaller_than_the_window(self) -> None:
        with pytest.raises(ValueError, match="overlap_chars"):
            chunk("text", max_chars=10, overlap_chars=10)

    def test_rejects_non_positive_window(self) -> None:
        with pytest.raises(ValueError, match="max_chars"):
            chunk("text", max_chars=0)

    def test_long_input_terminates(self) -> None:
        """Guards against the split loop failing to advance."""
        chunks = chunk("word " * 20000, max_chars=100, overlap_chars=99)
        assert len(chunks) > 1


class TestDeriveTitle:
    def test_prefers_a_markdown_heading(self) -> None:
        assert derive_title("# EKS Spot notes\n\nbody") == "EKS Spot notes"

    def test_falls_back_to_first_line(self) -> None:
        assert derive_title("\n\nplain first line\nsecond") == "plain first line"

    def test_uses_fallback_when_empty(self) -> None:
        assert derive_title("", fallback="notes.md") == "notes.md"

    def test_truncates(self) -> None:
        assert len(derive_title("x" * 500)) == 120


class TestExcerpt:
    def test_short_text_is_unchanged(self) -> None:
        assert excerpt("hello") == "hello"

    def test_collapses_whitespace(self) -> None:
        assert excerpt("a\n\nb   c") == "a b c"

    def test_truncates_on_a_word_boundary(self) -> None:
        result = excerpt("word " * 200, max_length=50)
        assert len(result) <= 50
        assert result.endswith("…")
        assert "  " not in result

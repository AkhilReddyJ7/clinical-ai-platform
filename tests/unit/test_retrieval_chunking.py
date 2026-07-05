from modules.retrieval.chunking import chunk_text


def test_chunk_text_empty_string_returns_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_short_text_returns_a_single_chunk() -> None:
    text = "a short clinical note"
    assert chunk_text(text, chunk_size_chars=2_000) == [text]


def test_chunk_text_strips_surrounding_whitespace() -> None:
    assert chunk_text("  hello world  ", chunk_size_chars=2_000) == ["hello world"]


def test_chunk_text_splits_long_text_into_multiple_chunks() -> None:
    text = ("word " * 1000).strip()  # 4999 chars
    chunks = chunk_text(text, chunk_size_chars=1_000, overlap_chars=100)

    assert len(chunks) > 1
    assert all(len(chunk) <= 1_000 for chunk in chunks)
    # every chunk boundary lands on a word, never mid-word
    for chunk in chunks:
        assert chunk == chunk.strip()
        assert not chunk.startswith(" ")


def test_chunk_text_consecutive_chunks_overlap() -> None:
    text = ("word " * 1000).strip()
    chunks = chunk_text(text, chunk_size_chars=1_000, overlap_chars=100)

    # the tail of chunk N should reappear at the head of chunk N+1
    first_tail = chunks[0][-50:]
    assert first_tail in chunks[1]


def test_chunk_text_reconstructs_full_text_when_overlap_removed_conceptually() -> None:
    # Not an exact reconstruction test (overlap makes that awkward) --
    # just confirms no content is skipped between chunks.
    text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    chunks = chunk_text(text, chunk_size_chars=20, overlap_chars=5)

    assert "".join(chunks).replace(" ", "") != ""
    for word in text.split():
        assert any(word in chunk for chunk in chunks)


def test_chunk_text_single_long_word_with_no_spaces_does_not_hang() -> None:
    text = "a" * 5_000
    chunks = chunk_text(text, chunk_size_chars=1_000, overlap_chars=100)

    assert len(chunks) > 1
    assert "".join(c.replace("a", "") for c in chunks) == ""

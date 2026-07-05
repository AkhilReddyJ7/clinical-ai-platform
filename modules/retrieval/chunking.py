def chunk_text(text: str, *, chunk_size_chars: int = 2_000, overlap_chars: int = 200) -> list[str]:
    """Splits text into overlapping, whitespace-snapped chunks (ADR-0034).

    Character-based, not token-based -- no new tokenizer dependency, the
    same posture as `anthropic_max_input_chars` already bounding the LLM
    call by character count. Clinical notes here rarely approach that cap,
    so most documents produce a handful of chunks, not hundreds.

    Precondition: overlap_chars < chunk_size_chars (otherwise start would
    never advance). Both defaults are chosen safely; not runtime-asserted,
    since every caller in this codebase passes settings-derived values.
    """
    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= chunk_size_chars:
        return [stripped]

    chunks: list[str] = []
    start = 0
    while start < len(stripped):
        end = start + chunk_size_chars
        if end < len(stripped):
            snap = stripped.rfind(" ", start, end)
            if snap > start:
                end = snap
        chunk = stripped[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(stripped):
            break
        start = end - overlap_chars
    return chunks

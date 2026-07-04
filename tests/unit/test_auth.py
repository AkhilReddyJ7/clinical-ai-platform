import pytest
from fastapi import HTTPException

from modules.auth.api_key import _parse_api_keys, _resolve_caller, require_api_key


def test_parses_label_key_pairs() -> None:
    assert _parse_api_keys("alice:a,bob:b") == {"a": "alice", "b": "bob"}


def test_trims_whitespace_around_labels_and_keys() -> None:
    assert _parse_api_keys(" alice : a , bob :b ") == {"a": "alice", "b": "bob"}


def test_empty_string_yields_no_keys() -> None:
    assert _parse_api_keys("") == {}


def test_ignores_empty_segments() -> None:
    assert _parse_api_keys("alice:a,,bob:b,") == {"a": "alice", "b": "bob"}


def test_drops_an_entry_with_no_label() -> None:
    # A bare key with no colon is a configuration error, not an anonymous
    # caller -- it's dropped, not silently accepted (ADR-0026).
    assert _parse_api_keys("bare-key,alice:a") == {"a": "alice"}


def test_drops_an_entry_with_an_empty_label_or_key() -> None:
    assert _parse_api_keys(":a,alice:,bob:b") == {"b": "bob"}


def test_splits_only_on_the_first_colon() -> None:
    # A key itself may legitimately contain a colon; only the label side
    # of the pair is delimited.
    assert _parse_api_keys("alice:sk:with:colons") == {"sk:with:colons": "alice"}


def test_later_duplicate_keys_win() -> None:
    assert _parse_api_keys("alice:shared,bob:shared") == {"shared": "bob"}


def test_resolve_caller_returns_the_label_for_a_matching_key() -> None:
    assert _resolve_caller("secret", {"secret": "alice", "other": "bob"}) == "alice"


def test_resolve_caller_returns_none_for_no_match() -> None:
    assert _resolve_caller("nope", {"secret": "alice"}) is None


def test_resolve_caller_returns_none_for_empty_valid_keys() -> None:
    assert _resolve_caller("anything", {}) is None


@pytest.mark.asyncio
async def test_require_api_key_returns_the_resolved_label() -> None:
    caller = await require_api_key(api_key="secret", valid_keys={"secret": "alice"})
    assert caller == "alice"


@pytest.mark.asyncio
async def test_require_api_key_raises_401_for_a_wrong_key() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_api_key(api_key="wrong", valid_keys={"secret": "alice"})
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_key_raises_401_for_a_missing_key() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_api_key(api_key=None, valid_keys={"secret": "alice"})
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_api_key_raises_503_when_nothing_is_configured() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_api_key(api_key="anything", valid_keys={})
    assert exc_info.value.status_code == 503

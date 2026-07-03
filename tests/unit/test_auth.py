from modules.auth.api_key import _matches_any, _parse_api_keys


def test_parses_comma_separated_keys() -> None:
    assert _parse_api_keys("a,b,c") == frozenset({"a", "b", "c"})


def test_trims_whitespace_around_keys() -> None:
    assert _parse_api_keys(" a , b ,c ") == frozenset({"a", "b", "c"})


def test_empty_string_yields_no_keys() -> None:
    assert _parse_api_keys("") == frozenset()


def test_ignores_empty_segments() -> None:
    assert _parse_api_keys("a,,b,") == frozenset({"a", "b"})


def test_matches_any_true_when_key_present() -> None:
    assert _matches_any("secret", frozenset({"secret", "other"}))


def test_matches_any_false_when_key_absent() -> None:
    assert not _matches_any("nope", frozenset({"secret"}))


def test_matches_any_false_for_empty_valid_set() -> None:
    assert not _matches_any("anything", frozenset())

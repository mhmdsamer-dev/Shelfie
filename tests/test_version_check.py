from shelfie.version_check import (
    _numeric_version_parts,
    check_for_newer_release,
    is_newer_version,
)


def test_numeric_version_parts_parses_semver():
    assert _numeric_version_parts("1.2.3") == (1, 2, 3)


def test_numeric_version_parts_accepts_v_prefix():
    assert _numeric_version_parts("v2.0.1") == (2, 0, 1)


def test_numeric_version_parts_handles_invalid_input():
    assert _numeric_version_parts("not-a-version") == ()


def test_is_newer_version_true_for_higher_patch():
    assert is_newer_version("1.2.4", "1.2.3") is True


def test_is_newer_version_true_when_trailing_zeroes_differ():
    assert is_newer_version("1.2", "1.1.9") is True


def test_is_newer_version_false_for_same_release():
    assert is_newer_version("1.2.0", "1.2") is False


def test_is_newer_version_false_for_older_release():
    assert is_newer_version("1.1.9", "1.2.0") is False


def test_check_for_newer_release_returns_latest_when_newer():
    assert check_for_newer_release("0.1.0", fetch_latest=lambda: "0.2.0") == "0.2.0"


def test_check_for_newer_release_returns_none_when_same():
    assert check_for_newer_release("0.2.0", fetch_latest=lambda: "0.2.0") is None


def test_check_for_newer_release_returns_none_when_fetch_fails():
    assert check_for_newer_release("0.2.0", fetch_latest=lambda: None) is None

"""The battle location must be selectable without editing the pinned default."""

import pytest

from native_runner.match_factory import (
    NATIVE_LOCATION_ENV,
    PINNED_DEFAULT_LOCATION_ID,
    MatchConfig,
    default_location_id,
)


def test_location_defaults_to_the_pinned_ladder_arena(monkeypatch):
    monkeypatch.delenv(NATIVE_LOCATION_ENV, raising=False)
    assert default_location_id() == PINNED_DEFAULT_LOCATION_ID
    assert MatchConfig().location == PINNED_DEFAULT_LOCATION_ID


def test_location_can_be_overridden_for_base_content(monkeypatch):
    monkeypatch.setenv(NATIVE_LOCATION_ENV, "15000002")
    assert default_location_id() == 15_000_002
    assert MatchConfig().location == 15_000_002


def test_location_override_accepts_hex_and_rejects_junk(monkeypatch):
    monkeypatch.setenv(NATIVE_LOCATION_ENV, "0xE4E1C2")
    assert default_location_id() == 15_000_002
    for value in ("", "0", "-4", "arena"):
        monkeypatch.setenv(NATIVE_LOCATION_ENV, value)
        if value == "":
            assert default_location_id() == PINNED_DEFAULT_LOCATION_ID
            continue
        with pytest.raises(ValueError, match=NATIVE_LOCATION_ENV):
            default_location_id()


def test_explicit_location_still_wins(monkeypatch):
    monkeypatch.setenv(NATIVE_LOCATION_ENV, "15000002")
    assert MatchConfig(location=15_000_199).location == 15_000_199

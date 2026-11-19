"""Translated update errors match their strings.json entries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.singapore.errors import (
    ERROR_FETCH,
    ERROR_HTTP,
    ERROR_PAYLOAD,
    MESSAGES,
    source_error,
)

_STRINGS = json.loads(
    (Path(__file__).parents[1] / "custom_components/singapore/strings.json").read_text()
)


@pytest.mark.parametrize("key", sorted(MESSAGES))
def test_message_matches_strings_json(key):
    assert _STRINGS["exceptions"][key]["message"] == MESSAGES[key]


def test_source_error_is_translated():
    err = source_error(ERROR_HTTP, "psi", status=429)
    assert isinstance(err, UpdateFailed)
    assert str(err) == "Air quality (PSI) returned HTTP 429"
    assert err.translation_kwargs == {
        "translation_domain": "singapore",
        "translation_key": "http_error",
        "translation_placeholders": {"source": "Air quality (PSI)", "status": "429"},
    }


def test_source_error_placeholders_are_strings():
    err = source_error(ERROR_FETCH, "coe", error=TimeoutError("slow"))
    assert str(err) == "Could not fetch COE bidding results: slow"
    placeholders = err.translation_kwargs["translation_placeholders"]
    assert all(isinstance(value, str) for value in placeholders.values())


def test_unknown_source_falls_back_to_its_key():
    err = source_error(ERROR_PAYLOAD, "somewhere", detail="no records")
    assert str(err) == "somewhere sent data in an unexpected format: no records"

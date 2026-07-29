"""Checks on strings.json / translations that mirror what hassfest enforces.

CI runs hassfest, which rejects a manifest whose translations contain literal
URLs (they belong in `description_placeholders`). These tests catch the same
mistakes locally, plus the ones hassfest cannot see: the two files drifting
apart, and a step or error code the flow can produce having no text at all.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from string import Formatter

from custom_components.singapore.config_flow import CONF_SESSION_COOKIE

_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "singapore"
_STRINGS = _COMPONENT / "strings.json"
_EN = _COMPONENT / "translations" / "en.json"

_URL_RE = re.compile(r"https?://")

# Placeholders the flow fills in for each step (see config_flow.py).
_EXPECTED_PLACEHOLDERS = {
    "sp_login": {"authorize_url"},
    "reauth_confirm": {"authorize_url"},
    "sp_session": {"auth0_url"},
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_strings(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_strings(value, f"{path}.{key}" if path else key)
    elif isinstance(node, str):
        yield path, node


def test_translations_mirror_strings():
    """en.json must mirror strings.json exactly."""
    assert _load(_EN) == _load(_STRINGS)


def test_no_literal_urls_in_strings():
    """hassfest rejects URLs in translations; use a placeholder instead."""
    offenders = [
        path for path, value in _walk_strings(_load(_STRINGS)) if _URL_RE.search(value)
    ]
    assert offenders == []


def test_step_placeholders_are_supplied_by_the_flow():
    """A placeholder the flow does not fill renders as a literal brace."""
    strings = _load(_STRINGS)
    for section in ("config", "options"):
        for step_id, step in strings[section]["step"].items():
            used = {
                name
                for _, name, _, _ in Formatter().parse(step.get("description", ""))
                if name
            }
            assert used == _EXPECTED_PLACEHOLDERS.get(step_id, set()), (
                f"{section}.{step_id} placeholders"
            )


def test_every_flow_step_and_error_has_text():
    """Each step the flow can show, and each error it can raise, needs text."""
    strings = _load(_STRINGS)
    expected_steps = {
        "config": {"user", "sp_login", "sp_account", "sp_session", "reauth_confirm"},
        "options": {"init", "sp_login", "sp_account", "sp_session"},
    }
    expected_errors = {
        "config": {
            "empty_name",
            "name_too_long",
            "invalid_callback",
            "cannot_connect",
            "no_accounts",
            "invalid_session_cookie",
        },
        "options": {
            "invalid_callback",
            "cannot_connect",
            "no_accounts",
            "invalid_session_cookie",
        },
    }
    for section, steps in expected_steps.items():
        assert steps <= set(strings[section]["step"])
        assert expected_errors[section] <= set(strings[section]["error"])

    # The menu the options flow shows must label every branch it offers.
    assert set(strings["options"]["step"]["init"]["menu_options"]) == {
        "sp_login",
        "sp_session",
        "sp_unlink",
    }


def test_input_fields_are_labelled():
    """Every field the user types into needs a label in both files."""
    strings = _load(_STRINGS)
    assert CONF_SESSION_COOKIE in strings["config"]["step"]["sp_session"]["data"]
    assert CONF_SESSION_COOKIE in strings["options"]["step"]["sp_session"]["data"]
    assert "callback_url" in strings["config"]["step"]["sp_login"]["data"]
    assert "callback_url" in strings["config"]["step"]["reauth_confirm"]["data"]

"""Translated update errors for the public data sources.

Each key is an ``exceptions`` entry in strings.json, so Home Assistant shows
the message in the user's language. The English text here is also the
exception's own message (logs, diagnostics, the repair issue's last error);
tests/test_errors.py checks it matches strings.json.
"""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN, SOURCE_NAMES

ERROR_HTTP = "http_error"
ERROR_FETCH = "fetch_failed"
ERROR_PAYLOAD = "bad_payload"

MESSAGES: dict[str, str] = {
    # The source answered with an error status (often a 429 or a 5xx).
    ERROR_HTTP: "{source} returned HTTP {status}",
    # The request itself failed (network down, timeout, TLS).
    ERROR_FETCH: "Could not fetch {source}: {error}",
    # The source answered, but not with data in the shape we parse.
    ERROR_PAYLOAD: "{source} sent data in an unexpected format: {detail}",
}


def source_error(key: str, source: str, **placeholders: object) -> UpdateFailed:
    """An ``UpdateFailed`` for a data source (a ``const.SOURCES`` key)."""
    values = {
        "source": SOURCE_NAMES.get(source, source),
        **{name: str(value) for name, value in placeholders.items()},
    }
    return UpdateFailed(
        MESSAGES[key].format(**values),
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=values,
    )

"""Tests for config flow constants."""

from homeassistant.const import CONF_NAME

from custom_components.singapore.config_flow import STEP_USER_DATA_SCHEMA


def test_schema_has_name_field():
    """Config flow schema includes a name field."""
    assert CONF_NAME in STEP_USER_DATA_SCHEMA.schema


def test_manifest_pins_the_same_sp_services_release_as_the_docs():
    import json
    from pathlib import Path

    from custom_components.singapore.const import (
        SP_SERVICES_EXPORT_COMMAND,
        SP_SERVICES_REQUIREMENT,
    )

    root = Path(__file__).parent.parent
    manifest = json.loads(
        (root / "custom_components/singapore/manifest.json").read_text()
    )
    assert SP_SERVICES_REQUIREMENT in manifest["requirements"]
    assert SP_SERVICES_REQUIREMENT in SP_SERVICES_EXPORT_COMMAND
    assert SP_SERVICES_EXPORT_COMMAND in (root / "docs/sp_services.md").read_text()

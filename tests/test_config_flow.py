"""Tests for config flow constants."""

from homeassistant.const import CONF_NAME

from custom_components.singapore.config_flow import STEP_USER_DATA_SCHEMA


def test_schema_has_name_field():
    """Config flow schema includes a name field defaulting to Singapore."""
    assert CONF_NAME in STEP_USER_DATA_SCHEMA.schema
    assert STEP_USER_DATA_SCHEMA({}) == {CONF_NAME: "Singapore"}

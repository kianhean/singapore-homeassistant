"""Tests for config flow constants."""
from custom_components.singapore.config_flow import STEP_USER_DATA_SCHEMA
from homeassistant.const import CONF_NAME


def test_schema_has_name_field():
    """Config flow schema includes a name field."""
    assert CONF_NAME in STEP_USER_DATA_SCHEMA.schema

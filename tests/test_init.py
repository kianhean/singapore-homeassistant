"""Tests for integration constants and domain."""

from custom_components.singapore import DOMAIN, PLATFORMS


def test_domain():
    assert DOMAIN == "singapore"


def test_platforms_include_weather():
    assert "weather" in PLATFORMS


def test_platforms_include_calendar():
    assert "calendar" in PLATFORMS

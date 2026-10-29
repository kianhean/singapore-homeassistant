"""Tests for the SP Services account coordinator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.helpers import issue_registry as ir
from sp_services import (
    ApiError,
    SessionCookie,
    SessionExpiredError,
    SessionState,
    TokenSet,
    UsageData,
    UsagePoint,
)

from custom_components.singapore.const import (
    CONF_ACCOUNT_NO,
    CONF_IMPORT_STATISTICS,
    CONF_SESSION,
    SUBENTRY_SP_ACCOUNT,
)
from custom_components.singapore.sp_services_coordinator import (
    SP_TIMEZONE,
    SpServicesCoordinator,
    derive_usage,
)

ACCOUNT = "8949049293"


def _session(value: str = "v1") -> str:
    return SessionState(
        cookies=[SessionCookie(name="auth0", value=value, domain="identity.x")]
    ).to_string()


def _usage(**overrides) -> UsageData:
    fields = {
        "electricity_today_kwh": None,
        "electricity_month_kwh": None,
        "water_today_m3": None,
        "water_month_m3": None,
        "account_no": ACCOUNT,
        "last_updated": datetime(2026, 10, 29, 12, tzinfo=SP_TIMEZONE),
    }
    fields.update(overrides)
    return UsageData(**fields)


NOW = datetime(2026, 10, 29, 12, 0, tzinfo=SP_TIMEZONE)


# --- derive_usage --------------------------------------------------------


def test_derive_prefers_history_over_null_summary_fields():
    usage = _usage(
        electricity_daily_history=[
            UsagePoint("2026-10-28", 15.2),
            UsagePoint("2026-10-29", 9.5),
        ],
        electricity_monthly_history=[
            UsagePoint("2026-09-01", 572.0, "Actual"),
            UsagePoint("2026-10-01", 410.0, "Estimated"),
        ],
        water_monthly_history=[
            UsagePoint("2026-09-01", 33.9, "Actual"),
            UsagePoint("2026-10-01", 31.0, "Estimated"),
        ],
    )

    data = derive_usage(usage, NOW)

    assert data.electricity_today == 9.5
    assert data.electricity_this_month == 410.0
    assert data.electricity_last_month == 572.0
    assert data.water_this_month == 31.0
    assert data.water_last_month == 33.9


def test_derive_matches_day_first_periods():
    usage = _usage(
        electricity_daily_history=[UsagePoint("29/10/2026", 7.0)],
        water_monthly_history=[UsagePoint("01/09/2026", 33.9)],
    )

    data = derive_usage(usage, NOW)

    assert data.electricity_today == 7.0
    assert data.water_last_month == 33.9


def test_derive_uses_singapore_date_not_utc():
    # 00:30 SGT on 30 Oct is still 29 Oct in UTC.
    now_utc = datetime(2026, 10, 29, 16, 30, tzinfo=UTC)
    usage = _usage(
        electricity_daily_history=[
            UsagePoint("2026-10-29", 15.0),
            UsagePoint("2026-10-30", 0.4),
        ]
    )

    assert derive_usage(usage, now_utc).electricity_today == 0.4


def test_derive_today_falls_back_to_half_hourly_sum():
    usage = _usage(
        electricity_hourly_history=[
            UsagePoint("2026-10-29 00:00:00", 0.5),
            UsagePoint("2026-10-29 00:30:00", 0.25),
            UsagePoint("2026-10-28 23:30:00", 9.0),
        ]
    )

    assert derive_usage(usage, NOW).electricity_today == 0.75


def test_derive_month_sums_daily_only_when_complete():
    complete = [UsagePoint(f"2026-10-{d:02d}", 1.0) for d in range(1, 30)]
    assert derive_usage(
        _usage(electricity_daily_history=complete), NOW
    ).electricity_this_month == pytest.approx(29.0)

    gappy = [p for p in complete if p.period != "2026-10-05"]
    assert (
        derive_usage(
            _usage(electricity_daily_history=gappy), NOW
        ).electricity_this_month
        is None
    )


def test_derive_falls_back_to_summary_and_skips_none_values():
    usage = _usage(
        electricity_month_kwh=123.0,
        water_last_month_m3=30.0,
        water_monthly_history=[UsagePoint("2026-09-01", None, "Estimated")],
    )

    data = derive_usage(usage, NOW)

    assert data.electricity_this_month == 123.0
    assert data.water_last_month == 30.0
    assert data.electricity_today is None
    assert data.water_this_month is None


# --- coordinator ---------------------------------------------------------


def _make(import_statistics: bool = False):
    hass = MagicMock()
    subentry = ConfigSubentry(
        data={
            CONF_ACCOUNT_NO: ACCOUNT,
            CONF_SESSION: _session("v1"),
            CONF_IMPORT_STATISTICS: import_statistics,
        },
        subentry_type=SUBENTRY_SP_ACCOUNT,
        title=f"SP Services {ACCOUNT}",
        unique_id=ACCOUNT,
    )
    entry = ConfigEntry(subentries=[subentry])
    coordinator = SpServicesCoordinator(hass, entry, subentry)
    client = MagicMock()
    client.token_set = None
    client.close = AsyncMock()
    coordinator._client = client
    return hass, entry, subentry, coordinator, client


def _tokens(expired: bool = False) -> TokenSet:
    offset = timedelta(hours=-1 if expired else 1)
    return TokenSet(access_token="tok", expires_at=datetime.now(UTC) + offset)


@pytest.fixture(autouse=True)
def _reset_issue_mocks():
    ir.async_create_issue.reset_mock()
    ir.async_delete_issue.reset_mock()


async def test_renews_when_no_token_and_persists_rolled_session():
    hass, entry, subentry, coordinator, client = _make()

    async def _renew():
        client.token_set = _tokens()
        return client.token_set

    client.renew_silently = AsyncMock(side_effect=_renew)
    client.export_session.return_value = SessionState.from_string(_session("v2"))
    client.fetch_usage = AsyncMock(return_value=_usage())

    data = await coordinator._async_update_data()

    assert data.account_no == ACCOUNT
    client.renew_silently.assert_awaited_once()
    client.fetch_usage.assert_awaited_once_with("tok", account_no=ACCOUNT)
    hass.config_entries.async_update_subentry.assert_called_once()
    _, kwargs = hass.config_entries.async_update_subentry.call_args
    assert kwargs["data"][CONF_SESSION] == _session("v2")
    assert kwargs["data"][CONF_ACCOUNT_NO] == ACCOUNT
    ir.async_delete_issue.assert_called_once()


async def test_valid_token_is_reused_without_renewal():
    hass, _, _, coordinator, client = _make()
    client.token_set = _tokens()
    client.renew_silently = AsyncMock()
    client.fetch_usage = AsyncMock(return_value=_usage())

    await coordinator._async_update_data()

    client.renew_silently.assert_not_awaited()
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_unchanged_session_is_not_written_back():
    hass, _, _, coordinator, client = _make()
    client.token_set = _tokens(expired=True)
    client.renew_silently = AsyncMock(return_value=_tokens())
    client.export_session.return_value = SessionState.from_string(_session("v1"))
    client.fetch_usage = AsyncMock(return_value=_usage())

    await coordinator._async_update_data()

    client.renew_silently.assert_awaited_once()
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_rejected_token_is_renewed_once_and_retried():
    _, _, _, coordinator, client = _make()
    client.token_set = _tokens()
    client.renew_silently = AsyncMock(return_value=TokenSet(access_token="new"))
    client.export_session.return_value = SessionState.from_string(_session("v1"))
    client.fetch_usage = AsyncMock(side_effect=[SessionExpiredError("401"), _usage()])

    await coordinator._async_update_data()

    assert client.fetch_usage.await_args_list[-1].args == ("new",)


async def test_ended_session_raises_repair_issue():
    from homeassistant.helpers.update_coordinator import UpdateFailed

    _, _, _, coordinator, client = _make()
    client.renew_silently = AsyncMock(side_effect=SessionExpiredError("ended"))

    with pytest.raises(UpdateFailed, match="log in again"):
        await coordinator._async_update_data()

    ir.async_create_issue.assert_called_once()
    args, kwargs = ir.async_create_issue.call_args
    assert args[2] == f"sp_session_expired_{coordinator.subentry_id}"
    assert kwargs["translation_placeholders"] == {"account_no": ACCOUNT}
    ir.async_delete_issue.assert_not_called()


async def test_api_error_is_update_failed_without_issue():
    from homeassistant.helpers.update_coordinator import UpdateFailed

    _, _, _, coordinator, client = _make()
    client.token_set = _tokens()
    client.fetch_usage = AsyncMock(side_effect=ApiError("HTTP 500"))

    with pytest.raises(UpdateFailed, match="HTTP 500"):
        await coordinator._async_update_data()

    ir.async_create_issue.assert_not_called()


async def test_malformed_stored_session_raises_repair_issue():
    from homeassistant.helpers.update_coordinator import UpdateFailed

    _, _, _, coordinator, _ = _make()
    coordinator._client = None

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    ir.async_create_issue.assert_called_once()


async def test_statistics_imported_only_when_enabled():
    for enabled in (False, True):
        hass, _, _, coordinator, client = _make(import_statistics=enabled)
        client.token_set = _tokens()
        usage = _usage()
        client.fetch_usage = AsyncMock(return_value=usage)

        with patch(
            "custom_components.singapore.sp_services_coordinator."
            "async_import_usage_statistics",
            AsyncMock(),
        ) as imp:
            await coordinator._async_update_data()

        if enabled:
            imp.assert_awaited_once_with(hass, ACCOUNT, usage)
        else:
            imp.assert_not_awaited()


async def test_subentry_update_with_new_session_rebuilds_client():
    _, _, subentry, coordinator, old_client = _make()
    coordinator.async_request_refresh = AsyncMock()

    # Same session (the coordinator's own write-back): nothing happens.
    await coordinator.async_subentry_updated(subentry)
    assert coordinator._client is old_client
    coordinator.async_request_refresh.assert_not_awaited()

    new = ConfigSubentry(
        data={**subentry.data, CONF_SESSION: _session("v9")},
        subentry_type=SUBENTRY_SP_ACCOUNT,
        title=subentry.title,
    )
    await coordinator.async_subentry_updated(new)

    old_client.close.assert_awaited_once()
    assert coordinator._client is not old_client
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize(
    "label", ["Sep 2026", "September 2026", "Sep-26", "2026-09", "09/2026", "202609"]
)
def test_derive_matches_month_only_labels(label):
    usage = _usage(electricity_monthly_history=[UsagePoint(label, 572.0, "Actual")])

    assert derive_usage(usage, NOW).electricity_last_month == 572.0


def test_month_label_does_not_count_as_a_day():
    # "Oct 2026" is a month total, never today's usage, even on the 1st.
    usage = _usage(electricity_daily_history=[UsagePoint("Oct 2026", 410.0)])
    first = datetime(2026, 10, 1, 12, tzinfo=SP_TIMEZONE)

    assert derive_usage(usage, first).electricity_today is None

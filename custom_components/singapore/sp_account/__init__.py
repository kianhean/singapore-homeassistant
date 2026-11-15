"""SP Group e-account protocol layer (no Home Assistant imports).

Vendored from maci0/home-assistant-sp (https://github.com/maci0/home-assistant-sp)
by Marcel W. Wysocki, MIT licensed (see LICENSE in this directory). It talks to
the same Auth0 + Jarvis + Njord APIs as the SP Group app and turns the replies
into sensor specs and Energy statistics. Local changes, kept minimal so upstream
fixes can be pulled in:

- ``const.DOMAIN`` is ``singapore`` (recorder statistics must use the
  integration domain as their source).
- ``history.external_statistic_id`` folds a premise id to the characters the
  recorder accepts.
- The executor thread name prefix.

The Home Assistant glue lives one level up in ``sp_account_coordinator.py``,
``sp_account_sensor.py`` and ``config_flow.py``.
"""

# Singapore Home Assistant HACS Integration

## Project Overview

A HACS (Home Assistant Community Store) custom integration for Home Assistant.
The integration domain is `singapore_hello` and currently provides a hello world sensor.

## Structure

```
custom_components/singapore_hello/   # Integration source
tests/                               # Pytest test suite
.github/workflows/                   # CI workflows
```

## Development Setup

```bash
pip install -r requirements_test.txt
```

## Running Tests

```bash
pytest tests/ -v
```

Run with coverage:

```bash
pytest tests/ -v --cov=custom_components/singapore_hello --cov-report=term-missing
```

## Adding a New Platform

1. Create `custom_components/singapore_hello/<platform>.py`
2. Add the platform to `PLATFORMS` in `__init__.py`
3. Add tests in `tests/test_<platform>.py`

## Key Conventions

- All HA I/O must be `async`; use `async_` prefixed HA methods
- Entity unique IDs must be stable: `{entry_id}_{suffix}`
- Keep `manifest.json` version in sync with releases
- Translations live in `translations/en.json` and must mirror `strings.json`

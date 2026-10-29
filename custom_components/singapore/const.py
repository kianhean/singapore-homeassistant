"""Constants for the Singapore integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "singapore"

# SP Services account subentries (household electricity / water usage).
SUBENTRY_SP_ACCOUNT: Final = "sp_account"
CONF_SESSION: Final = "session"
CONF_ACCOUNT_NO: Final = "account_no"
CONF_IMPORT_STATISTICS: Final = "import_statistics"

SP_SERVICES_TAG: Final = "v1.3.0"
# A tag archive rather than git+https, so neither HA nor the user's computer
# needs git installed.
SP_SERVICES_REQUIREMENT: Final = (
    "sp-services @ https://github.com/kianhean/sp_api/archive/refs/tags/"
    f"{SP_SERVICES_TAG}.zip"
)
SP_SERVICES_EXPORT_COMMAND: Final = (
    f'uvx --from "{SP_SERVICES_REQUIREMENT}" '
    "sp-services-e2e --export-session --no-token-cache"
)

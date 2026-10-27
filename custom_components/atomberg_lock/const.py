"""Constants for the Atomberg Smart Lock integration."""

from typing import Final

DOMAIN: Final[str] = "atomberg_lock"
MANUFACTURER: Final[str] = "Atomberg"
DEFAULT_NAME: Final[str] = "Atomberg SL1 Pro"

# Config entries
CONF_MAC: Final[str] = "mac_address"
CONF_MASTER_KEY: Final[str] = "master_key"
CONF_SALT: Final[str] = "salt"
CONF_SLOT_MAPPINGS: Final[str] = "slot_mappings"
CONF_PAIRING_METHOD: Final[str] = "pairing_method"

METHOD_NATIVE: Final[str] = "native"
METHOD_CLOUD: Final[str] = "cloud"
METHOD_MANUAL: Final[str] = "manual"

# Physical bolt auto-relock latch timeout
AUTO_LOCK_SECONDS: Final[int] = 5

"""pyatomberg: Python library for Atomberg smart door locks and appliances."""

from pyatomberg.auth.cognito import CognitoOAuthHandler
from pyatomberg.auth.developer import DeveloperTokenHandler
from pyatomberg.client import AtombergClient
from pyatomberg.exceptions import (
    AtombergAuthError,
    AtombergBLEError,
    AtombergConnectionError,
    AtombergCryptoError,
    AtombergDeviceOfflineError,
    AtombergError,
    AtombergPairingError,
)
from pyatomberg.fan.device import AtombergFan
from pyatomberg.fan.udp_listener import AtombergUDPListener
from pyatomberg.lock.cloud_sync import AtombergCloudSync
from pyatomberg.lock.device import AtombergLock
from pyatomberg.lock.dynamic_pin import AtombergDynamicPinClient
from pyatomberg.lock.pairing import (
    AtombergBLEPairingManager,
    PairingResult,
    extract_keys_from_btsnoop,
)
from pyatomberg.models.device import (
    AtombergDeviceModel,
    DeviceType,
    FanState,
    LockState,
    WaterPurifierState,
)

__version__ = "0.1.0"

__all__ = [
    "AtombergLock",
    "AtombergBLEPairingManager",
    "PairingResult",
    "extract_keys_from_btsnoop",
    "AtombergCloudSync",
    "AtombergDynamicPinClient",
    "AtombergClient",
    "AtombergFan",
    "AtombergUDPListener",
    "DeveloperTokenHandler",
    "CognitoOAuthHandler",
    "AtombergError",
    "AtombergAuthError",
    "AtombergConnectionError",
    "AtombergDeviceOfflineError",
    "AtombergBLEError",
    "AtombergCryptoError",
    "AtombergPairingError",
    "AtombergDeviceModel",
    "DeviceType",
    "FanState",
    "LockState",
    "WaterPurifierState",
]

"""Exceptions for pyatomberg."""

class AtombergError(Exception):
    """Base exception for all pyatomberg errors."""


class AtombergAuthError(AtombergError):
    """Raised when authentication fails or token is expired/invalid."""


class AtombergConnectionError(AtombergError):
    """Raised when communication with cloud or local device fails."""


class AtombergDeviceOfflineError(AtombergConnectionError):
    """Raised when a device is offline or unreachable."""


class AtombergBLEError(AtombergError):
    """Raised when a Bluetooth Low Energy operation fails."""


class AtombergCryptoError(AtombergError):
    """Raised when cryptographic operations or CRC verification fails."""


class AtombergPairingError(AtombergError):
    """Raised when lock pairing or key exchange fails."""

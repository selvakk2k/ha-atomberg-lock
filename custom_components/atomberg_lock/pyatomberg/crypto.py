"""Cryptographic and CRC routines for Atomberg protocols."""

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from pyatomberg.exceptions import AtombergCryptoError


def calculate_modbus_crc16(data: bytes) -> int:
    """Calculate Modbus CRC-16 checksum (Polynomial 0xA001, Initial 0xFFFF).

    Returns integer in range 0x0000 - 0xFFFF.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def verify_modbus_crc16(data_with_crc: bytes) -> bool:
    """Verify data ending with a 2-byte little-endian Modbus CRC-16."""
    if len(data_with_crc) < 2:
        return False
    payload = data_with_crc[:-2]
    expected_crc = int.from_bytes(data_with_crc[-2:], byteorder="little")
    return calculate_modbus_crc16(payload) == expected_crc


def encrypt_aes_ecb(plaintext: bytes, key: bytes, pad: bool = True) -> bytes:
    """Encrypt plaintext using AES-128 in ECB mode.

    Key must be exactly 16 bytes.
    If pad is True, PKCS#7 padding is applied.
    """
    if len(key) != 16:
        raise AtombergCryptoError(f"AES-128 key must be exactly 16 bytes, got {len(key)}")

    if pad:
        padder = padding.PKCS7(128).padder()
        padded_data = padder.update(plaintext) + padder.finalize()
    else:
        if len(plaintext) % 16 != 0:
            raise AtombergCryptoError(
                f"Plaintext length ({len(plaintext)}) must be a multiple of 16 when pad=False"
            )
        padded_data = plaintext

    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(padded_data) + encryptor.finalize()


def decrypt_aes_ecb(ciphertext: bytes, key: bytes, unpad: bool = True) -> bytes:
    """Decrypt ciphertext using AES-128 in ECB mode.

    Key must be exactly 16 bytes. Ciphertext length must be a multiple of 16.
    If unpad is True, PKCS#7 unpadding is removed.
    """
    if len(key) != 16:
        raise AtombergCryptoError(f"AES-128 key must be exactly 16 bytes, got {len(key)}")
    if len(ciphertext) == 0 or len(ciphertext) % 16 != 0:
        raise AtombergCryptoError(
            f"Ciphertext length ({len(ciphertext)}) must be a non-zero multiple of 16"
        )

    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(ciphertext) + decryptor.finalize()

    if unpad:
        try:
            unpadder = padding.PKCS7(128).unpadder()
            return unpadder.update(decrypted) + unpadder.finalize()
        except ValueError as err:
            raise AtombergCryptoError(f"PKCS#7 unpadding failed: {err}") from err

    return decrypted

"""Offline Credential and Key Extraction for Atomberg Smart Locks.

Extracts master encryption keys and salts from BTSnoop HCI snoop logs.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import struct
from typing import Any

from pyatomberg.const import HSJ_FACTORY_KEY, HSJ_HEADER
from pyatomberg.crypto import calculate_modbus_crc16, decrypt_aes_ecb
from pyatomberg.exceptions import AtombergCryptoError, AtombergPairingError

_LOGGER = logging.getLogger(__name__)


@dataclass
class PairingResult:
    """Credentials generated or extracted for an Atomberg smart door lock."""
    mac_address: str
    master_key: bytes
    salt: bytes
    name: str | None = None
    model: str | None = None
    temp_auth_code: str | None = None
    general_auth_code: str | None = None

    @property
    def master_key_hex(self) -> str:
        """Hexadecimal representation of 16-byte master key."""
        return self.master_key.hex()

    @property
    def salt_hex(self) -> str:
        """Hexadecimal representation of 4-byte lock salt."""
        return self.salt.hex()


def extract_keys_from_btsnoop(file_path: str | Path) -> PairingResult:
    """Parse an Android/Linux BTSnoop HCI packet log to extract lock credentials.

    Args:
        file_path: Path to the .log, .btsnoop, or .pcap HCI log file.

    Returns:
        PairingResult containing extracted MAC, Master Key, and Salt.

    Raises:
        FileNotFoundError: If the log file cannot be located.
        AtombergCryptoError: If the file is not a valid BTSnoop format.
        AtombergPairingError: If credentials cannot be recovered from packets.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"BTSnoop file not found: {file_path}")

    raw_data = path.read_bytes()
    if len(raw_data) < 16 or not raw_data.startswith(b"btsnoop\x00"):
        raise AtombergCryptoError("Invalid BTSnoop file header (expected 'btsnoop\\0')")

    records: list[bytes] = []
    offset = 16
    while offset + 24 <= len(raw_data):
        orig_len, inc_len, flags, drops, ts = struct.unpack(">IIIIq", raw_data[offset : offset + 24])
        offset += 24
        packet = raw_data[offset : offset + inc_len]
        offset += inc_len
        records.append(packet)

    def _parse_att(packet: bytes) -> dict[str, Any] | None:
        p = packet
        if len(p) >= 10 and p.startswith(b"\x00\x00"):
            p = p[4:]
        if len(p) < 9:
            return None
        packet_type = p[0]
        if packet_type != 0x02:
            return None
        acl_length = struct.unpack("<H", p[3:5])[0]
        payload = p[5 : 5 + acl_length]
        if len(payload) < 5 or struct.unpack("<H", payload[2:4])[0] != 4:
            return None
        att = payload[4:]
        if not att:
            return None
        if att[0] == 0x52:
            return {"direction": "TX", "value": att[3:]}
        if att[0] == 0x1B:
            return {"direction": "RX", "value": att[3:]}
        return None

    frames: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for r in records:
        att = _parse_att(r)
        if not att:
            continue
        val = att["value"]
        if val.startswith(HSJ_HEADER) and len(val) >= 5:
            declared = int.from_bytes(val[3:5], "big")
            current = {
                "direction": att["direction"],
                "expected": declared,
                "data": bytearray(val),
            }
        elif current is not None:
            if att["direction"] == current["direction"]:
                current["data"].extend(val)
        else:
            continue

        if current and len(current["data"]) >= current["expected"]:
            frames.append({
                "direction": current["direction"],
                "frame": bytes(current["data"][: current["expected"]]),
            })
            current = None

    def _decrypt(frame: bytes, key: bytes) -> bytes | None:
        if len(frame) < 9 or not frame.startswith(HSJ_HEADER):
            return None
        declared = int.from_bytes(frame[3:5], "big")
        if declared != len(frame):
            return None
        body = frame[:-2]
        received_crc = int.from_bytes(frame[-2:], "big")
        if received_crc != calculate_modbus_crc16(body):
            return None
        try:
            return decrypt_aes_ecb(frame[7:-2], key, unpad=True)
        except Exception:
            return None

    lock_mac: str | None = None
    master_key: bytes | None = None
    lock_salt: bytes | None = None

    for item in frames:
        candidate_keys = [HSJ_FACTORY_KEY]
        if master_key:
            candidate_keys.append(master_key)

        plaintext: bytes | None = None
        for k in candidate_keys:
            res = _decrypt(item["frame"], k)
            if res and len(res) >= 8:
                op = int.from_bytes(res[5:7], "big")
                if op in [0x00F0, 0x00F1, 0x00F2, 0x00F3, 0x00C6, 0x02C6, 0x0007, 0x000D]:
                    plaintext = res
                    break

        if not plaintext:
            continue

        op = int.from_bytes(plaintext[5:7], "big")

        # Capture Master Key from F1 response
        if op == 0x00F1 and item["direction"] == "RX" and len(plaintext) >= 29:
            if not master_key:
                master_key = plaintext[13:29]

        # Extract MAC and Salt from 0x00C6 TLV payload
        if op == 0x00C6 and item["direction"] == "RX":
            idx = 13
            while idx + 2 < len(plaintext):
                tag_len = plaintext[idx]
                tag_type = plaintext[idx + 1]
                val = plaintext[idx + 2 : idx + tag_len + 1]
                if tag_type == 0x01 and len(val) == 6:
                    lock_mac = ":".join(f"{b:02X}" for b in val)
                if tag_type == 0x09 and len(val) == 4:
                    lock_salt = val
                idx += tag_len + 1

        # Extract Salt from 0x00F2 request fallback
        if op == 0x00F2 and item["direction"] == "TX" and len(plaintext) >= 17:
            cand_salt = plaintext[13:17]
            if cand_salt != b"\x00\x00\x00\x00":
                lock_salt = cand_salt

    if not master_key or not lock_salt:
        raise AtombergPairingError("Could not extract Master Key or Salt from BTSnoop log")

    return PairingResult(
        mac_address=lock_mac or "UNKNOWN",
        master_key=master_key,
        salt=lock_salt,
    )

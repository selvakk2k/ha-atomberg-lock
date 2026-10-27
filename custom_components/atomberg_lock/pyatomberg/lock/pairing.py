"""Native Zero-Snoop BLE Pairing Engine and BTSnoop Fallback Extractor for Atomberg Smart Locks.

Credits:
- Reverse-engineered protocol definitions by dreadedlama (https://github.com/dreadedlama/atomberg-lock)
  under GNU General Public License v3.0.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import secrets
import struct
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from pyatomberg.const import HSJ_FACTORY_KEY, HSJ_HEADER, LOCK_NOTIFY_CHAR_UUID, LOCK_WRITE_CHAR_UUID
from pyatomberg.crypto import calculate_modbus_crc16, decrypt_aes_ecb, encrypt_aes_ecb
from pyatomberg.exceptions import AtombergBLEError, AtombergCryptoError, AtombergPairingError
from pyatomberg.lock.hsj_protocol import (
    OP_KEY_EXCHANGE,
    OP_PROBE_HANDSHAKE,
    build_hsj_packet,
    parse_hsj_packet,
)

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


class AtombergBLEPairingManager:
    """Manages direct native pairing with an unbonded Atomberg lock over Bluetooth."""

    def __init__(self, factory_key: bytes = HSJ_FACTORY_KEY) -> None:
        """Initialize pairing manager."""
        self.factory_key = factory_key

    async def discover_unpaired_locks(self, timeout: float = 10.0) -> list[BLEDevice]:
        """Scan for Atomberg locks in pairing mode."""
        devices = await BleakScanner.discover(timeout=timeout)
        found: list[BLEDevice] = []
        for dev in devices:
            name = (dev.name or "").lower()
            if "atomberg" in name or "hsj" in name or "sl1" in name:
                found.append(dev)
        return found

    async def pair_device(
        self,
        target: str | BLEDevice,
        custom_key: bytes | None = None,
        custom_salt: bytes | None = None,
        timeout: float = 15.0,
    ) -> PairingResult:
        """Pair natively with an unbonded lock without requiring phone logs or ADB."""
        new_master_key = custom_key or secrets.token_bytes(16)
        new_salt = custom_salt or secrets.token_bytes(4)

        if len(new_master_key) != 16:
            raise AtombergPairingError("Master key must be exactly 16 bytes")
        if len(new_salt) != 4:
            raise AtombergPairingError("Lock salt must be exactly 4 bytes")

        mac_address = target if isinstance(target, str) else target.address
        client = BleakClient(target, timeout=timeout)
        recv_queue: asyncio.Queue[bytes] = asyncio.Queue()
        rx_buffer = bytearray()
        expected_len = 0

        def _notification_cb(sender: Any, data: bytearray) -> None:
            nonlocal expected_len, rx_buffer
            raw = bytes(data)
            if raw.startswith(HSJ_HEADER) and len(raw) >= 5:
                expected_len = int.from_bytes(raw[3:5], byteorder="big")
                rx_buffer = bytearray(raw)
            else:
                rx_buffer.extend(raw)

            if expected_len > 0 and len(rx_buffer) >= expected_len:
                full_frame = bytes(rx_buffer[:expected_len])
                encrypted_payload = full_frame[7 : expected_len - 2]
                recv_queue.put_nowait(encrypted_payload)
                rx_buffer = bytearray(rx_buffer[expected_len:])
                expected_len = 0

        try:
            await client.connect()
            await client.start_notify(LOCK_NOTIFY_CHAR_UUID, _notification_cb)
            await asyncio.sleep(0.5)

            # Step 1: Probe unbonded handshake with FACTORY_KEY
            probe_cmd = bytes.fromhex("000000000200f000000000000c")
            packet = build_hsj_packet(probe_cmd, self.factory_key, frame_seq=2)

            for i in range(0, len(packet), 20):
                await client.write_gatt_char(LOCK_WRITE_CHAR_UUID, packet[i : i + 20], response=False)
                await asyncio.sleep(0.03)

            cipher_resp = await asyncio.wait_for(recv_queue.get(), timeout=5.0)
            dec = decrypt_aes_ecb(cipher_resp, self.factory_key, unpad=True)
            if len(dec) < 8 or dec[5:7] != OP_PROBE_HANDSHAKE:
                raise AtombergPairingError("Unbonded handshake was rejected by lock")

            session_token = dec[:4]

            # Step 2: Provision new Master Key and Salt
            pair_cmd = (
                session_token
                + bytes.fromhex("0300f100000000000c")
                + new_master_key
                + new_salt
            )
            pair_packet = build_hsj_packet(pair_cmd, self.factory_key, frame_seq=2)

            for i in range(0, len(pair_packet), 20):
                await client.write_gatt_char(
                    LOCK_WRITE_CHAR_UUID, pair_packet[i : i + 20], response=False
                )
                await asyncio.sleep(0.03)

            confirm_resp = await asyncio.wait_for(recv_queue.get(), timeout=5.0)
            dec_confirm = decrypt_aes_ecb(confirm_resp, self.factory_key, unpad=True)
            if len(dec_confirm) < 8 or dec_confirm[5:7] != OP_KEY_EXCHANGE:
                raise AtombergPairingError("Lock failed to confirm new key provisioning")

            _LOGGER.info("Successfully provisioned new pairing keys to lock: %s", mac_address)
            return PairingResult(
                mac_address=mac_address.upper(),
                master_key=new_master_key,
                salt=new_salt,
            )

        except Exception as err:
            raise AtombergPairingError(f"Native pairing failed for {mac_address}: {err}") from err
        finally:
            if client.is_connected:
                try:
                    await client.stop_notify(LOCK_NOTIFY_CHAR_UUID)
                    await client.disconnect()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# BTSnoop Log Parser Fallback
# ---------------------------------------------------------------------------


def extract_keys_from_btsnoop(file_path: str | Path) -> PairingResult:
    """Parse an Android btsnoop_hci.log file to extract lock MAC, Master Key, and Salt."""
    data = Path(file_path).read_bytes()
    if not data.startswith(b"btsnoop"):
        raise AtombergCryptoError("Invalid BTSnoop file header")

    records: list[dict[str, Any]] = []
    offset = 16
    number = 0

    while offset + 24 <= len(data):
        orig_len, cap_len, flags, drops, ts = struct.unpack(">IIIIQ", data[offset : offset + 24])
        offset += 24
        if offset + cap_len > len(data):
            break

        packet = data[offset : offset + cap_len]
        offset += cap_len
        number += 1
        records.append({"number": number, "data": packet})

    def _parse_att(rec: dict[str, Any]) -> dict[str, Any] | None:
        p = rec["data"]
        if len(p) < 9 or p[0] != 0x02:
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

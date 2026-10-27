"""Atomberg smart door lock (SL1 / SL1 Pro) Bluetooth controller.

Based on protocol reverse engineering by dreadedlama (https://github.com/dreadedlama/atomberg-lock)
under GNU General Public License v3.0.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from bleak import BleakClient
from bleak.backends.device import BLEDevice

try:
    from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
    HAVE_BLEAK_RETRY = True
except ImportError:
    HAVE_BLEAK_RETRY = False

from pyatomberg.crypto import decrypt_aes_ecb
from pyatomberg.exceptions import AtombergBLEError
from pyatomberg.lock.hsj_protocol import (
    DEFAULT_SLOT_MAPPINGS,
    NOTIFY_UUID,
    OP_BATTERY,
    OP_CONTEXT_SYNC,
    OP_KEY_EXCHANGE,
    OP_LIVE_EVENT,
    OP_LOG_COUNT,
    OP_LOG_PAGE,
    OP_PROBE_HANDSHAKE,
    OP_TIME_SYNC,
    OP_UNLOCK,
    WRITE_UUID,
    LiveEvent,
    build_hsj_packet,
    parse_hsj_packet,
    parse_live_event,
    parse_log_record,
)
from pyatomberg.models.device import AtombergDeviceModel, DeviceType, LockState

_LOGGER = logging.getLogger(__name__)

COMMAND_TIMEOUT = 10.0


class AtombergLock(AtombergDeviceModel):
    """Represents an Atomberg smart door lock (SL1 / SL1 Pro) controlled over local BLE."""

    def __init__(
        self,
        mac_address: str,
        master_key: bytes,
        salt: bytes,
        name: str = "Atomberg Smart Lock",
        ble_device: BLEDevice | None = None,
        slot_mappings: dict[int, str] | None = None,
    ) -> None:
        """Initialize an AtombergLock instance."""
        super().__init__(
            device_id=mac_address.upper(),
            name=name,
            device_type=DeviceType.LOCK,
        )
        self.mac_address = mac_address.upper()
        self.master_key = master_key
        self.salt = salt
        self.ble_device = ble_device
        self.slot_mappings = slot_mappings or dict(DEFAULT_SLOT_MAPPINGS)

        self.state = LockState()
        self.client: BleakClient | None = None
        self.session_token: bytes | None = None
        self.session_key: bytes | None = None
        self.seq_counter: int = 5

        self._rx_buffer = bytearray()
        self._expected_len = 0
        self._recv_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._event_callbacks: list[Callable[[LiveEvent], None]] = []
        self._state_callbacks: list[Callable[["AtombergLock"], None]] = []

    @property
    def is_connected(self) -> bool:
        """Return True if BLE client is currently connected."""
        return self.client is not None and self.client.is_connected

    def register_event_callback(self, callback: Callable[[LiveEvent], None]) -> None:
        """Register a callback for decoded live lock events."""
        if callback not in self._event_callbacks:
            self._event_callbacks.append(callback)

    def register_state_callback(self, callback: Callable[["AtombergLock"], None]) -> None:
        """Register a callback for state changes."""
        if callback not in self._state_callbacks:
            self._state_callbacks.append(callback)

    def _clear_buffers(self) -> None:
        """Reset internal frame buffers and receive queue."""
        self._rx_buffer.clear()
        self._expected_len = 0
        while not self._recv_queue.empty():
            try:
                self._recv_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _notification_handler(self, sender: Any, data: bytearray) -> None:
        """Handle incoming GATT notifications from 0xFFF2."""
        raw = bytes(data)
        if raw.startswith(b"HSJ") and len(raw) >= 5:
            self._expected_len = int.from_bytes(raw[3:5], byteorder="big")
            self._rx_buffer = bytearray(raw)
        else:
            self._rx_buffer.extend(raw)

        if self._expected_len > 0 and len(self._rx_buffer) >= self._expected_len:
            full_frame = bytes(self._rx_buffer[: self._expected_len])
            encrypted_payload = full_frame[7 : self._expected_len - 2]

            # Check for unsolicited live notifications (0x000A)
            if self.session_key:
                try:
                    dec = decrypt_aes_ecb(encrypted_payload, self.session_key, unpad=True)
                    if len(dec) >= 7 and ((dec[5] << 8) | dec[6]) == OP_LIVE_EVENT:
                        live_event = parse_live_event(dec, self.slot_mappings)
                        if live_event:
                            self._apply_live_event(live_event)
                except Exception as err:
                    _LOGGER.debug("Error decoding live event: %s", err)

            self._recv_queue.put_nowait(encrypted_payload)
            self._rx_buffer = bytearray(self._rx_buffer[self._expected_len :])
            self._expected_len = 0

    def _apply_live_event(self, event: LiveEvent) -> None:
        """Update lock state from a live event."""
        if event.is_unlocked:
            self.state.is_locked = False
            self.state.last_unlock_method = event.credential_type
            self.state.last_unlock_user_id = event.slot_id
            self.state.last_unlock_timestamp = event.raw_timestamp

        if event.is_auto_lock:
            self.state.is_locked = True
            self.state.auto_lock_engaged = True

        if event.is_tamper:
            self.state.tamper_alert = True

        if event.battery is not None and 0 <= event.battery <= 100:
            self.state.battery_level = event.battery

        for ecb in self._event_callbacks:
            try:
                ecb(event)
            except Exception as err:
                _LOGGER.exception("Error in lock event callback: %s", err)

        for scb in self._state_callbacks:
            try:
                scb(self)
            except Exception as err:
                _LOGGER.exception("Error in lock state callback: %s", err)

    async def _send_frame(self, payload: bytes, key: bytes, frame_seq: int = 2) -> None:
        """Chunk packet into 20-byte MTUs and write to 0xFFF1."""
        if not self.client or not self.client.is_connected:
            raise AtombergBLEError("Cannot write frame: lock is not connected")

        packet = build_hsj_packet(payload, key, frame_seq=frame_seq)
        for i in range(0, len(packet), 20):
            chunk = packet[i : i + 20]
            await self.client.write_gatt_char(WRITE_UUID, chunk, response=False)
            await asyncio.sleep(0.03)

    async def _execute_step(
        self,
        payload: bytes,
        key: bytes,
        frame_seq: int,
        expected_op: bytes | None = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> bytes:
        """Send command frame and await decrypted response matching expected opcode."""
        await self._send_frame(payload, key, frame_seq)
        start_t = time.time()

        while (time.time() - start_t) < timeout:
            remaining = timeout - (time.time() - start_t)
            try:
                cipher_resp = await asyncio.wait_for(
                    self._recv_queue.get(), timeout=max(remaining, 0.1)
                )
            except asyncio.TimeoutError:
                break

            dec = decrypt_aes_ecb(cipher_resp, key, unpad=True)
            if expected_op is not None and len(dec) >= 8:
                if dec[5:7] == expected_op:
                    return dec
                continue
            return dec

        raise AtombergBLEError(
            f"Timed out waiting for lock response (expected op {expected_op.hex() if expected_op else 'any'})"
        )

    async def connect(self, timeout: float = 15.0) -> None:
        """Connect to lock over BLE and start notifications."""
        target: str | BLEDevice = self.ble_device if self.ble_device else self.mac_address

        try:
            if HAVE_BLEAK_RETRY and isinstance(target, BLEDevice):
                self.client = await establish_connection(
                    BleakClientWithServiceCache,
                    target,
                    self.mac_address,
                    max_attempts=3,
                    use_services_cache=True,
                )
            else:
                self.client = BleakClient(target, timeout=timeout)
                await self.client.connect()

            self._clear_buffers()
            await self.client.start_notify(NOTIFY_UUID, self._notification_handler)
            await asyncio.sleep(0.5)
            self.is_online = True
            _LOGGER.debug("Connected to Atomberg lock: %s", self.mac_address)
        except Exception as err:
            self.is_online = False
            raise AtombergBLEError(f"Failed to connect to lock {self.mac_address}: {err}") from err

    async def disconnect(self) -> None:
        """Disconnect from lock."""
        if self.client:
            try:
                if self.client.is_connected:
                    await self.client.stop_notify(NOTIFY_UUID)
                    await self.client.disconnect()
            except Exception as err:
                _LOGGER.debug("Error disconnecting: %s", err)
            finally:
                self.client = None
                self.is_online = False
                self.session_token = None
                self.session_key = None
                self._clear_buffers()

    async def authenticate(self) -> None:
        """Execute 3-way cryptographic session handshake (0x00F0 -> 0x00F1 -> 0x00F2)."""
        # Step 1: Handshake Probe 0x00F0
        probe_cmd = bytes.fromhex("000000000200f000000000000c")
        resp_f0 = await self._execute_step(
            probe_cmd,
            self.master_key,
            frame_seq=2,
            expected_op=OP_PROBE_HANDSHAKE,
        )
        self.session_token = resp_f0[:4]

        # Step 2: Key Exchange 0x00F1
        key_req = self.session_token + bytes.fromhex("0300f100000000000c")
        resp_f1 = await self._execute_step(
            key_req,
            self.master_key,
            frame_seq=2,
            expected_op=OP_KEY_EXCHANGE,
        )
        if len(resp_f1) < 29:
            raise AtombergBLEError(f"Invalid session key response length: {len(resp_f1)}")
        self.session_key = resp_f1[13:29]

        # Step 3: Context Synchronization 0x00F2 with Lock Salt
        sync_cmd = (
            self.session_token
            + bytes.fromhex("0400f2000003e9000c")
            + self.salt
        )
        await self._execute_step(
            sync_cmd,
            self.session_key,
            frame_seq=3,
            expected_op=OP_CONTEXT_SYNC,
        )
        self.seq_counter = 5
        _LOGGER.debug("Successfully authenticated with lock: %s", self.mac_address)

    async def unlock(self) -> int:
        """Unlock the smart door lock."""
        if not self.session_key or not self.session_token:
            await self.authenticate()

        assert self.session_token is not None
        assert self.session_key is not None

        ts_bytes = int(time.time()).to_bytes(4, byteorder="big")

        # Step 1: Synchronize lock clock 0x0007
        time_cmd = (
            self.session_token
            + bytes.fromhex("010007000003e9000c")
            + ts_bytes
            + bytes.fromhex("004d58")
        )
        await self._execute_step(
            time_cmd,
            self.session_key,
            frame_seq=3,
            expected_op=OP_TIME_SYNC,
        )

        # Step 2: Dispatch Unlock 0x0001
        unlock_payload = (
            self.session_token
            + bytes.fromhex("070001060103e9001500000006")
            + ts_bytes
            + bytes.fromhex("004d5800000000")
        )
        unlock_dec = await self._execute_step(
            unlock_payload,
            self.session_key,
            frame_seq=3,
            expected_op=OP_UNLOCK,
        )

        acknowledged = (
            len(unlock_dec) >= 8
            and unlock_dec[5:7] == OP_UNLOCK
            and unlock_dec[7] == 0x01
        )
        if not acknowledged:
            raise AtombergBLEError(f"Unlock command rejected by lock: {unlock_dec.hex(' ')}")

        self.state.is_locked = False
        self.state.last_unlock_method = "Atomberg App (BLE)"
        self.state.last_unlock_timestamp = time.time()
        return int.from_bytes(ts_bytes, byteorder="big")

    async def get_battery(self) -> int | None:
        """Poll lock battery percentage via 0x000D."""
        if not self.session_key or not self.session_token:
            await self.authenticate()

        assert self.session_token is not None
        assert self.session_key is not None

        status_cmd = (
            self.session_token
            + bytes([self.seq_counter])
            + bytes.fromhex("000d000003e9000c00000000")
        )
        self.seq_counter = (self.seq_counter + 1) & 0xFF

        try:
            resp_dec = await self._execute_step(
                status_cmd,
                self.session_key,
                frame_seq=3,
                expected_op=OP_BATTERY,
                timeout=4.0,
            )
        except Exception as err:
            _LOGGER.debug("Failed to poll battery: %s", err)
            return None

        battery = None
        if b"MX" in resp_dec:
            mx_idx = resp_dec.index(b"MX")
            if mx_idx + 2 < len(resp_dec):
                battery = resp_dec[mx_idx + 2]
        elif len(resp_dec) >= 30:
            battery = resp_dec[29]
        elif len(resp_dec) >= 29:
            battery = resp_dec[28]

        if battery is not None and 0 <= battery <= 100:
            self.state.battery_level = battery
            return battery
        return None

    async def fetch_incremental_logs(self, start_offset: int = 0) -> list[dict[str, Any]]:
        """Fetch unread hardware event logs from lock flash storage."""
        if not self.session_key or not self.session_token:
            await self.authenticate()

        assert self.session_token is not None
        assert self.session_key is not None

        self.seq_counter = 7
        count_req = (
            self.session_token
            + bytes([self.seq_counter])
            + bytes.fromhex("0008000003e9000c")
        )
        self.seq_counter = (self.seq_counter + 1) & 0xFF

        count_dec = await self._execute_step(
            count_req,
            self.session_key,
            frame_seq=3,
            expected_op=OP_LOG_COUNT,
        )
        total_records = int.from_bytes(count_dec[-2:], byteorder="big")
        if total_records <= start_offset:
            return []

        new_records: list[dict[str, Any]] = []
        curr_offset = start_offset

        while curr_offset < total_records:
            batch_size = min(5, total_records - curr_offset)
            fetch_req = (
                self.session_token
                + bytes([self.seq_counter])
                + bytes.fromhex("0009010003e9001f")
                + curr_offset.to_bytes(2, byteorder="big")
                + batch_size.to_bytes(2, byteorder="big")
            )
            self.seq_counter = (self.seq_counter + 1) & 0xFF

            log_dec = await self._execute_step(
                fetch_req,
                self.session_key,
                frame_seq=3,
                expected_op=OP_LOG_PAGE,
            )

            records_data = log_dec[15:]
            num_in_page = len(records_data) // 32
            if num_in_page == 0:
                break

            for i in range(num_in_page):
                rec = records_data[i * 32 : (i + 1) * 32]
                if len(rec) != 32 or rec[4] == 0x3D:
                    continue
                parsed = parse_log_record(rec, self.slot_mappings)
                parsed["count"] = start_offset + len(new_records) + 1
                new_records.append(parsed)

            curr_offset += batch_size

        return new_records

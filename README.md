# Atomberg Smart Lock (`ha-atomberg-lock`)

[![Hardware Verified](https://img.shields.io/badge/Hardware-Verified%20(SL1%20Pro)-success.svg)](#hardware-compatibility)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![HACS: Custom](https://img.shields.io/badge/HACS-Custom%20Integration-orange.svg)](https://hacs.xyz)

Custom Home Assistant integration for local Bluetooth Low Energy (BLE) control and monitoring of **Atomberg Smart Door Locks** (SL1, SL1 Pro).

Provides direct offline locking, unlocking, battery reporting, and physical credential recognition (fingerprints, keycards, PINs) via Home Assistant's native Bluetooth stack or ESPHome Bluetooth Proxies.

---

## Table of Contents

- [Features](#features)
- [Hardware Compatibility](#hardware-compatibility)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [Method 1: HACS (Recommended)](#method-1-hacs-recommended)
  - [Method 2: Manual Installation](#method-2-manual-installation)
- [Configuration & Onboarding](#configuration--onboarding)
- [Entities Provided](#entities-provided)
- [Managing Credential Names](#managing-credential-names)
- [Remote & Timed OTP Generation](#remote--timed-otp-generation)
- [Troubleshooting](#troubleshooting)
- [Credits & License](#credits--license)

---

## Features

- **Direct Offline BLE Control**: High-speed, local AES-128 encrypted unlocking with automatic relock tracking.
- **ESPHome Proxy Support**: Operates smoothly across any ESPHome Bluetooth Proxy in your home.
- **Universal Cloud Account Sync**: Seamlessly imports existing lock encryption keys and credentials from your Atomberg account via AWS Cognito SSO.
- **Credential Identification**: Hardware audit log parser decodes which user or slot unlocked the door (fingerprint, NFC keycard, PIN, or mobile app).
- **Interactive Options Wizard**: 2-step setup to assign friendly names to discovered fingerprint and card slots.
- **Remote & Timed OTP Actions**: Generates single-use remote OTPs and expirable timed guest PINs directly from Home Assistant.
- **Battery Reporting**: Tracks battery percentage with periodic queries and refresh buttons.

---

## Hardware Compatibility

| Model | Series | Protocol | Hardware Verified |
| :--- | :--- | :--- | :--- |
| **Atomberg SL1** | `smart_lock_black` | HSJ BLE AES-128 | ✅ Hardware Verified |
| **Atomberg SL1 Pro** | `smart_lock_black` | HSJ BLE AES-128 | ✅ Hardware Verified |
| **Atomberg G1** | `cypheo_elite` | HSJ BLE AES-128 | Experimental |

---

## Prerequisites

1. Home Assistant 2024.1.0 or newer.
2. An active Bluetooth adapter on your Home Assistant host, or one or more [ESPHome Bluetooth Proxies](https://esphome.io/components/bluetooth_proxy.html) within range of the lock.
3. Lock already enrolled in the official Atomberg app (used for biometric enrollment).

---

## Installation

### Method 1: HACS (Recommended)

1. Open **HACS** in your Home Assistant sidebar.
2. Click the top-right three dots menu and select **Custom repositories**.
3. Paste repository URL: `https://github.com/selvakk2k/ha-atomberg-lock`
4. Select category: **Integration**.
5. Click **Add**, locate **Atomberg Smart Lock**, and click **Download**.
6. Restart Home Assistant Core.

### Method 2: Manual Installation

1. Download the latest release from the [Releases](https://github.com/selvakk2k/ha-atomberg-lock/releases) page.
2. Copy the `custom_components/atomberg_lock` folder into your Home Assistant `<config>/custom_components/` directory:
   ```
   config/
   └── custom_components/
       └── atomberg_lock/
   ```
3. Restart Home Assistant Core.

---

## Configuration & Onboarding

1. In Home Assistant, navigate to **Settings -> Devices & Services -> Add Integration**.
2. Search for **Atomberg Smart Lock**.
3. Select your setup method:
   - **Atomberg Cloud Account (Recommended)**:
     - Click the provided **[Click here to log in to Atomberg]** link.
     - Sign in to your account in your browser (supports Email, Google, Apple, or Facebook).
     - Copy the redirected URL (`https://app.atomberg-iot.com/?code=...`) from your address bar and paste it into the dialog.
     - Home Assistant automatically retrieves your lock's Bluetooth MAC address, Master Key, and Salt.
   - **Manual Key Entry**:
     - Advanced users can enter their 12-digit MAC address, 16-byte Master Key, and 4-byte Salt directly.

---

## Entities Provided

| Entity ID | Type | Description |
| :--- | :--- | :--- |
| `lock.atomberg_sl1_pro` | Lock | Controls lock bolt with auto-relock tracking. |
| `sensor.atomberg_sl1_pro_battery` | Sensor | Battery level percentage (0–100%). |
| `sensor.atomberg_sl1_pro_last_user` | Sensor | Displays friendly name of last enrolled slot or user. |
| `sensor.atomberg_sl1_pro_last_unlock_method` | Sensor | Credential type used (Fingerprint, NFC Card, PIN, App). |
| `sensor.atomberg_sl1_pro_last_event_time` | Sensor | Timestamp of the most recent lock event. |
| `button.atomberg_sl1_pro_sync_status` | Button | Queries battery level and synchronizes flash audit logs. |

---

## Managing Credential Names

When someone unlocks your door with a fingerprint, keycard, or PIN, Home Assistant detects the slot number (e.g. Slot 14).

To assign user-friendly names:
1. Go to **Settings -> Devices & Services -> Atomberg Smart Lock**.
2. Click the **Configure (Settings Cog)** button.
3. **Step 1**: Choose the slot from the dropdown list (e.g. `Slot 14`) or pick `Add Custom Slot Number...`.
4. **Step 2**: Enter the desired name (e.g. `Selva Right Thumb` or `Guest Card`) and submit.

The `sensor.*_last_user` entity will immediately display the assigned name whenever that credential is used.

---

## Remote & Timed OTP Generation

Atomberg locks support offline algorithmic OTP keypad PINs that guests can enter directly on the door lock:

- **Remote OTP (One-Time Use)**: Valid for 15 minutes. It works strictly once and is spent immediately upon unlocking.
- **Timed OTP (Time-Bounded)**: Valid for a custom duration (e.g. 4 hours). Expire automatically if not used before the window closes.

---

## Troubleshooting

- **Lock shows unavailable**: Locks enter low-power sleep when idle. The integration maintains availability state and wakes the device upon command dispatch. Ensure your Bluetooth adapter or ESPHome proxy has clear signal (RSSI > -85 dBm).
- **Connection timeouts**: If multiple Bluetooth clients attempt connections simultaneously, the lock may reject secondary sessions. Ensure only Home Assistant (or the official app) is communicating with the lock at any given moment.

---

## Credits & License

- Based on protocol reverse-engineering by **[dreadedlama/atomberg-lock](https://github.com/dreadedlama/atomberg-lock)**.
- Companion Python library: **[`atomberg-py`](https://github.com/selvakk2k/atomberg-py)**.
- Licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See [LICENSE](LICENSE) for details.

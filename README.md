# Atomberg Smart Lock (`ha-atomberg-lock`)

[![Hardware Verified](https://img.shields.io/badge/Hardware-Verified%20(SL1%20Pro)-success.svg)](#hardware-compatibility)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![HACS: Custom](https://img.shields.io/badge/HACS-Custom%20Integration-orange.svg)](https://hacs.xyz)

Reference custom Home Assistant integration for local Bluetooth Low Energy (BLE) control and monitoring of **Atomberg Smart Door Locks** (SL1, SL1 Pro).

> [!NOTE]
> **Reference Demo & Adoption Notice**: Offline Bluetooth lock control was originally reverse-engineered and established by **[@dreadedlama](https://github.com/dreadedlama/atomberg-lock)**, which is the foundational and older community integration for Atomberg smart door locks.
> 
> The core missing puzzle piece introduced by this project and [`atomberg-py`](https://github.com/selvakk2k/atomberg-py) is **automated Cloud-Assisted Key Extraction & AWS Cognito SSO Login**. Previously, users had to resort to manual packet captures or phone analysis to obtain their lock's `master_key` and `salt`. This authentication and extraction workflow should ideally be adopted directly into **[@dreadedlama/atomberg-lock](https://github.com/dreadedlama/atomberg-lock)** so the entire community benefits from zero-hassle, 1-click lock onboarding.

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
- [Viewing Extracted Keys & Credentials](#viewing-extracted-keys--credentials)
- [Troubleshooting](#troubleshooting)
- [Credits & License](#credits--license)

---

## Features

- **Direct Offline BLE Control**: Local AES-128 encrypted unlocking with automatic relock tracking.
- **ESPHome Proxy Support**: Operates smoothly across any ESPHome Bluetooth Proxy in your home.
- **Universal Cloud Account Sync**: Seamlessly imports existing lock encryption keys and credentials from your Atomberg account via AWS Cognito SSO.
- **View Extracted Credentials**: Interactive options dialog to safely reveal and copy your lock's cryptographic keys for use in other setups.
- **Credential Identification**: Hardware audit log parser decodes which user or slot unlocked the door (fingerprint, NFC keycard, PIN, or mobile app).
- **Interactive Options Wizard**: 2-step setup to assign friendly names to discovered fingerprint and card slots.
- **Battery Reporting**: Tracks battery percentage with periodic queries and refresh buttons.
- **History Synchronization**: Pulls unlock events and flash logs on demand via the sync button.

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
     - Direct offline entry of your 12-digit MAC address, 16-byte Master Key, and 4-byte Salt.

---

## Entities Provided

| Entity ID | Type | Description |
| :--- | :--- | :--- |
| `lock.atomberg_sl1_pro` | Lock | Controls lock bolt with auto-relock tracking. |
| `sensor.atomberg_sl1_pro_battery` | Sensor | Battery level percentage (0–100%). |
| `sensor.atomberg_sl1_pro_last_user` | Sensor | Displays friendly name of last enrolled slot or user. |
| `sensor.atomberg_sl1_pro_last_unlock_method` | Sensor | Credential type used (Fingerprint, NFC Card, PIN, App). |
| `sensor.atomberg_sl1_pro_last_event` | Sensor | Description of the most recent hardware event and history attributes. |
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

## Viewing Extracted Keys & Credentials

Even if you prefer running **[@dreadedlama/atomberg-lock](https://github.com/dreadedlama/atomberg-lock)** or an offline ESPHome setup, you can use this integration as a **zero-effort key extraction utility** to retrieve your lock's offline encryption keys without needing a rooted phone or BLE packet sniffer:

1. Follow the [Configuration & Onboarding](#configuration--onboarding) wizard and select **Cloud-Assisted Local Setup** or **Atomberg Cloud Account**.
2. Sign in once with your Atomberg account. The integration securely extracts your lock parameters.
3. Navigate to **Settings -> Devices & Services -> Atomberg Smart Lock**.
4. Click the **Configure (Settings Cog)** button.
5. Select **View Device Credentials & Keys**.
6. The dialog displays your lock's credentials, masked (`••••••`) by default for privacy:
   - **Bluetooth MAC Address** (e.g. `AA:BB:CC:11:22:33`)
   - **Master Key** (16-byte cryptographic key)
   - **Lock Salt** (4-byte hex salt)
   - **General Auth Code** & **Temporary Auth Code**
7. Click the eye toggle icon next to any field to unmask or copy the plain-text key directly into your clipboard.

---

## Troubleshooting

- **Lock shows unavailable**: Locks enter low-power sleep when idle. The integration maintains availability state and wakes the device upon command dispatch. Ensure your Bluetooth adapter or ESPHome proxy has clear signal (RSSI > -85 dBm).
- **Connection timeouts**: If multiple Bluetooth clients attempt connections simultaneously, the lock may reject secondary sessions. Ensure only Home Assistant (or the official app) is communicating with the lock at any given moment.

---

## Credits & License

- Original reverse-engineering and BLE lock control protocol: **[@dreadedlama](https://github.com/dreadedlama/atomberg-lock)**.
- Automated cloud auth, key extraction, and reference integration: **[`atomberg-py`](https://github.com/selvakk2k/atomberg-py)** and **`ha-atomberg-lock`**.
- Licensed under the **GNU General Public License v3.0 (GPL-3.0)**. See [LICENSE](LICENSE) for details.

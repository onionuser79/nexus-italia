# NEXUS-ITALIA Gateway

Automated installer for the **NEXUS-ITALIA** gateway based on Raspberry Pi Zero 2W and a MeshCore USB Companion.

This repository automatically installs and configures:

- System dependencies
- Dedicated Python virtual environment
- `meshcore` Python library for persistent serial connection
- `config.yaml` configuration file
- `systemd` service `nexus-gateway`
- Automatic start at boot

## Requirements

- Raspberry Pi OS / Debian / Ubuntu (NO desktop)
- Internet access
- MeshCore USB Companion connected
- MQTT credentials (request at info@meshcoreitalia.it)

## Creating the NEXUS channel with its Secret Key

<img width="302" height="399" alt="nexus" src="https://github.com/user-attachments/assets/8b4a8b6f-4050-4015-a9d1-3f626b3de48f" />

Channel Name: Nexus

Secret Key: a45768ab48e203498edbc11b35cdfbd7

## Quick install

Clone the repository and run the script as root:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/onionuser79/nexus-italia.git nexus-italia
cd nexus-italia
sudo bash install_gateway.sh
```

The script prompts step by step for:

- Linux service user
- Companion serial port
- `gateway_id`
- Local radio settings
- MQTT host/port/credentials
- MeshCore channel name and number

## Verified test values

Working configuration already verified:

- `gateway_id`: `NEXUS-ITALIA-RM`
- Serial port: `/dev/ttyUSB0`
- MeshCore channel: `NEXUS`
- Channel number: `1`
- MQTT broker with username/password authentication
- Service started via `systemd`

## Useful commands

Service status:

```bash
sudo systemctl status nexus-gateway --no-pager
```

Live logs:

```bash
journalctl -u nexus-gateway -f
```

Restart:

```bash
sudo systemctl restart nexus-gateway
```

## Installed paths

- Application: `/opt/nexus-gateway`
- Configuration: `/opt/nexus-gateway/config.yaml`
- Service: `/etc/systemd/system/nexus-gateway.service`

---

## Architecture

### Persistent serial connection

The gateway maintains a **persistent serial connection** to the MeshCore USB Companion using the `meshcore` Python library. This replaces the previous approach of spawning `meshcli` subprocesses for each operation, which caused the Companion display to turn on at every poll cycle.

Key benefits:
- Serial port is opened **once** at startup and kept open for the lifetime of the service
- Incoming channel messages are received via **event subscription** (no repeated polling)
- The Companion display is no longer activated by routine gateway operations
- All gateway operations (sending, adverts, stats) use the same persistent connection

The gateway uses Python `asyncio` for all concurrent operations (heartbeat, beacons, adverts, message consumption).

---

## Advanced features

### 1. Automatic channel scope configuration

At gateway startup, the scope is automatically set on the Nexus channel via the persistent serial connection.

Configurable in `config.yaml`:

```yaml
channel_scope: "it-lom-mi"
```

If `channel_scope` is not present, the default value is `it-lom-mi`.

> **Note:** Starting from meshcore_py v2.3.5, the scope should be set without the `#` prefix (e.g. `it-lom-mi`, not `#it-lom-mi`), matching the app convention. The `#` was removed to avoid confusion with channel names that also start with `#`. (Credit: Armando Accardo IK2XYP)

### 2. Automatic Nexus channel provisioning

At startup, the gateway checks whether the Nexus private channel exists on the Companion at the configured `channel_number`. If the channel is missing or has a different name, the gateway automatically creates it using the secret key from `config.yaml`. This removes the need to manually configure the channel via the MeshCore app before first use.

The check also runs after a **Companion reboot** is detected, so the channel is re-created if the device loses its configuration.

Configurable in `config.yaml`:

```yaml
channel_name: NEXUS
channel_number: 1
channel_secret: "a45768ab48e203498edbc11b35cdfbd7"  # 32-char hex = 16 bytes
```

- `channel_secret` — the Nexus channel secret key as a 32-character hex string. If omitted or empty, the auto-provisioning is skipped (backward compatible with existing deployments where the channel was created manually).

### 3. Nexus channel filtering

The gateway only relays messages from the configured **Nexus channel** (`channel_number` in `config.yaml`). Messages received from the Public channel or any other channel are silently discarded. This ensures only Nexus traffic is bridged over MQTT, preventing unrelated mesh traffic from leaking to the Internet.

### 4. Companion clock sync

At startup, the gateway synchronizes the Companion's clock via `sync_time()`. This ensures accurate timestamps on all messages from the first moment the gateway is online.

### 5. Path hash mode configuration

At startup, the gateway configures `path.hash.mode` on the Companion. This controls the low-level ID/hash encoding size used during repeater adverts:

- Mode 0: 1-byte hash (256 unique IDs, 64 max flood)
- **Mode 1: 2-byte hash (65,536 unique IDs, 32 max flood)** — default
- Mode 2: 3-byte hash (16,777,216 unique IDs, 21 max flood)

Mode 1 is the recommended setting for networks running MeshCore firmware >= 1.14. The setting is applied automatically and re-applied after Companion reboot detection.

Configurable in `config.yaml`:

```yaml
path_hash_mode: 1  # 0=1byte, 1=2byte (default), 2=3byte
```

If `path_hash_mode` is not present in the config, the default value is `1`.

### 6. Companion reboot detection

The scope settings, clock, channel configuration, and path hash mode are lost if the Companion device reboots (power loss, USB disconnect, etc.). The gateway automatically detects reboots by monitoring the Companion uptime via `get_stats_core()` on the persistent connection. If a reboot is detected (uptime decreases compared to the previous reading), the following are re-applied immediately in order:

1. Clock sync
2. Nexus channel provisioning
3. Channel flood scope (`set_flood_scope`)
4. Default flood scope (`set_default_flood_scope`)
5. Path hash mode

This ensures messages are never sent without scope on the mesh, the Nexus channel is always present, the Companion clock is always accurate, and both scope settings and path hash mode are correctly configured, even after unexpected restarts.

### 7. Periodic RF beacon on the Nexus channel

The gateway periodically transmits a beacon message via RF on the Nexus channel.

Configurable parameters in `config.yaml` under `runtime`:

```yaml
runtime:
  beacon_interval_sec: 10800    # interval in seconds (default: 3 hours)
  beacon_channel: 2             # Nexus channel ID as seen by the Companion
  beacon_text: "NEXUS-ITALIA Gateway XX - meshcoreitalia.it"
```

- `beacon_interval_sec` — interval between beacons (default 10800 = 3 hours)
- `beacon_channel` — channel number to transmit the beacon on (default `2`, corresponding to the Nexus channel on the Companion)
- `beacon_text` — beacon text; if empty, the beacon is disabled

An initial beacon is also sent **10 seconds after startup**, to announce the gateway immediately on the RF network after a reboot.

### 8. Periodic advert (0hop and flood)

The gateway can periodically send advert commands to announce the Companion on the MeshCore network:

- **advert (0hop)** — local announcement, not propagated. Default: every **1 hour**.
- **floodadv (flood)** — announcement propagated across the mesh network. Default: every **3 hours**.

Configurable parameters in `config.yaml` under `runtime`:

```yaml
runtime:
  advert_enabled: true             # enable 0hop advert
  advert_interval_sec: 3600        # interval in seconds (default: 1 hour)
  flood_advert_enabled: true       # enable flood advert
  flood_advert_interval_sec: 10800 # interval in seconds (default: 3 hours)
```

Both adverts are also sent once at service startup (+15s and +20s respectively).

### 9. Default flood scope configuration

Starting from **meshcore >= 2.3.7**, the gateway also sets a **default flood scope** on the Companion at startup via `set_default_flood_scope()`. This is distinct from the per-channel flood scope set by `set_flood_scope()` (feature 1 above): the default scope applies as the device-level fallback for any channel that does not have an explicit scope configured.

Both the channel scope and the default scope use the same `channel_scope` value from `config.yaml`:

```yaml
channel_scope: "it-lom-mi"
```

The default flood scope is applied:
- At gateway startup, after the channel scope is set
- After a Companion reboot is detected (same re-apply sequence as all other settings)

This feature requires `meshcore >= 2.3.7` and `meshcore-cli >= 1.5.7`. (Credit: Armando Accardo IK2XYP)

### Full configuration example

```yaml
gateway_id: NEXUS-ITALIA-MI
site_name: "NEXUS-ITALIA Milano"
region: lombardia
mesh_id: mesh-mi
radio_band: "868"
channel_name: NEXUS
channel_number: 1
channel_scope: "it-lom-mi"
channel_secret: "a45768ab48e203498edbc11b35cdfbd7"
path_hash_mode: 1  # 0=1byte, 1=2byte (default), 2=3byte
protocol_version: "1.0"

meshcore:
  serial_port: /dev/ttyUSB0
  baudrate: 115200
  mode: serial

mqtt:
  host: nexus.meshcoreitalia.it
  port: 1883
  username: NEXUS-ITALIA-MI
  password: your_password
  keepalive: 30
  tls: false
  uplink_topic: nexus/v1/uplink
  downlink_topic: nexus/v1/downlink/NEXUS-ITALIA-MI
  heartbeat_topic: nexus/v1/heartbeat/NEXUS-ITALIA-MI
  status_topic: nexus/v1/status/NEXUS-ITALIA-MI

runtime:
  dedupe_ttl_sec: 180
  heartbeat_interval_sec: 30
  poll_interval_sec: 5
  log_level: INFO
  beacon_interval_sec: 10800
  beacon_channel: 2
  beacon_text: "NEXUS-ITALIA Gateway MI - meshcoreitalia.it"
  advert_enabled: true
  advert_interval_sec: 3600
  flood_advert_enabled: true
  flood_advert_interval_sec: 10800
```

---

## Software versioning

The gateway includes a software version number (`__version__` in `nexus_gateway/__init__.py`), separate from the MQTT `protocol_version` defined in `config.yaml`.

Both values are included in heartbeat payloads:

- `protocol_version` — MQTT message format version (from config, e.g. `"1.0"`)
- `software_version` — gateway software release (from code, e.g. `"2.1.0"`)

This allows tracking which software version is deployed on each gateway node.

---

## Operational notes

The install script adds the service user to the `dialout` group for serial port access.
After installation, if the Companion is not immediately detected by the service, a Raspberry Pi reboot may help.

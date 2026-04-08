# NEXUS-ITALIA Gateway Installer

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
git clone https://github.com/xpinguinx/nexus-italia.git
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
channel_scope: "it-lo"
```

If `channel_scope` is not present, the default value is `it-lo`.

> **Note:** Starting from meshcore_py v2.3.5, the scope should be set as `it-lo` without the `#` prefix, matching the app convention. The `#` was removed to avoid confusion with channel names that also start with `#`. (Credit: Armando Accardo)

### 2. Companion reboot detection

The scope setting is lost if the Companion device reboots (power loss, USB disconnect, etc.). The gateway automatically detects reboots by monitoring the Companion uptime via `get_stats_core()` on the persistent connection. If a reboot is detected (uptime decreases compared to the previous reading), the scope is re-applied immediately.

This ensures messages are never sent without scope on the mesh, even after unexpected Companion restarts.

### 3. Periodic RF beacon on the Nexus channel

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

### 4. Periodic advert (0hop and flood)

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

### Full configuration example

```yaml
gateway_id: NEXUS-ITALIA-MI
site_name: "NEXUS-ITALIA Milano"
region: lombardia
mesh_id: mesh-mi
radio_band: "868"
channel_name: NEXUS
channel_number: 1
channel_scope: "it-lo"
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

## Operational notes

The install script adds the service user to the `dialout` group for serial port access.
After installation, if the Companion is not immediately detected by the service, a Raspberry Pi reboot may help.

## Upgrading from meshcli-based versions

If you are upgrading from a previous version that used `meshcli` subprocesses:

1. Update the gateway files: `cd nexus-italia && git pull`
2. Re-run the installer or manually update the venv:
   ```bash
   cd /opt/nexus-gateway
   sudo -u <service-user> .venv/bin/pip install -r requirements.txt
   ```
3. Update `config.yaml`: rename the `meshcli:` section to `meshcore:` and remove the `command` and `timeout_sec` fields (the gateway also accepts the old `meshcli:` key for backward compatibility)
4. Restart the service: `sudo systemctl restart nexus-gateway`

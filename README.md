# NEXUS-ITALIA Gateway Installer

Automated installer for the **NEXUS-ITALIA** gateway based on Raspberry Pi Zero 2W and a MeshCore USB Companion.

This repository automatically installs and configures:

- System dependencies
- Dedicated Python virtual environment
- `meshcore-cli` inside the gateway virtualenv
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

## Scope and RF beacon configuration (branch `iw2ohx-improvements`)

This version introduces the following features:

### 1. Automatic channel scope configuration

At gateway startup, the scope is automatically set on the Nexus channel using the command:

```bash
meshcli -j -s /dev/ttyUSB0 -b 115200 scope "it-lo"
```

The scope is configurable in `config.yaml`:

```yaml
channel_scope: "it-lo"
```

If `channel_scope` is not present, the default value is `it-lo`.

> **Note:** Starting from meshcore_py v2.3.5, the scope should be set as `it-lo` without the `#` prefix, matching the app convention. The `#` was removed to avoid confusion with channel names that also start with `#`. (Credit: Armando Accardo)

### 2. Periodic RF beacon on the Nexus channel

The gateway periodically transmits a beacon message via RF on the Nexus channel, using the command:

```bash
meshcli -j -s /dev/ttyUSB0 -b 115200 chan 2 "beacon text"
```

Configurable parameters in `config.yaml` under the `runtime` section:

```yaml
runtime:
  beacon_interval_sec: 10800    # interval in seconds (default: 3 hours)
  beacon_channel: 2             # Nexus channel ID as seen by the Companion
  beacon_text: "NEXUS-ITALIA Gateway XX - meshcoreitalia.it"
```

- `beacon_interval_sec` — interval between beacons (default 10800 = 3 hours)
- `beacon_channel` — channel number to transmit the beacon on (default `2`, corresponding to the Nexus channel on the Companion)
- `beacon_text` — beacon text; if empty, the beacon is disabled

### 3. Initial beacon 10 seconds after startup

In addition to the periodic beacon, the gateway sends a first beacon **10 seconds after startup**, to announce itself immediately on the RF network after a reboot or power-on.

### 4. Periodic advert (0hop and flood)

The gateway can periodically send `advert` and `floodadv` commands to announce the Companion on the MeshCore network:

- **advert (0hop)** — local announcement, not propagated. Default: every **1 hour**.
- **floodadv (flood)** — announcement propagated across the mesh network. Default: every **3 hours**.

Configurable parameters in `config.yaml` under the `runtime` section:

```yaml
runtime:
  advert_enabled: true             # enable 0hop advert
  advert_interval_sec: 3600        # interval in seconds (default: 1 hour)
  flood_advert_enabled: true       # enable flood advert
  flood_advert_interval_sec: 10800 # interval in seconds (default: 3 hours)
```

Both adverts are also sent once at service startup (+15s and +20s respectively).

### Modified files

| File | Change |
|------|--------|
| `nexus_gateway/config.py` | Added `channel_scope`, beacon, advert and flood advert fields |
| `nexus_gateway/meshcli_adapter.py` | Added `set_scope()`, `send_beacon()`, `send_advert()`, `send_flood_advert()` methods |
| `nexus_gateway/service.py` | Scope at startup, beacon/advert/flood advert in separate threads |
| `config.example.yaml` | Documented all parameters with default values |

### Full beacon + advert configuration example

```yaml
channel_scope: "it-lo"

runtime:
  dedupe_ttl_sec: 180
  heartbeat_interval_sec: 30
  poll_interval_sec: 5
  log_level: INFO
  beacon_interval_sec: 10800
  beacon_channel: 2
  beacon_text: "NEXUS-ITALIA Gateway RM - meshcoreitalia.it"
  advert_enabled: true
  advert_interval_sec: 3600
  flood_advert_enabled: true
  flood_advert_interval_sec: 10800
```

---

## Operational notes

The install script adds the service user to the `dialout` group for serial port access.
After installation, if the Companion is not immediately detected by the service, a Raspberry Pi reboot may help.

## Manual MeshCore tests

```bash
sudo -u <service-user> /opt/nexus-gateway/.venv/bin/meshcli -j -s /dev/ttyUSB0 -b 115200 get_channels
sudo -u <service-user> /opt/nexus-gateway/.venv/bin/meshcli -j -s /dev/ttyUSB0 -b 115200 sync_msgs
```

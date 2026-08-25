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
sudo systemctl status nexus-gateway-v2 --no-pager
```

Live logs:

```bash
journalctl -u nexus-gateway-v2 -f
```

Restart:

```bash
sudo systemctl restart nexus-gateway-v2
```

## Installed paths

- Application: `/opt/nexus-gateway-v2`
- Configuration: `/opt/nexus-gateway-v2/config.yaml`
- Service: `/etc/systemd/system/nexus-gateway-v2.service`

## Development deploy

The `deploy.sh` script deploys local changes to the gateway host. It auto-detects
whether it is already running on macmini and deploys **directly**, rather than
SSHing to itself through a relay.

```bash
bash nexus-italia/deploy.sh              # auto-detect (direct when on macmini)
bash nexus-italia/deploy.sh local        # force direct deploy
bash nexus-italia/deploy.sh macmini-lan  # force relay via macmini-lan (LAN)
bash nexus-italia/deploy.sh macmini-ext  # force relay (internet/mobile)
```

Detection uses macmini's static LAN address (en9 wired `192.168.1.127`, en1
Wi-Fi `192.168.1.128`), **not** the hostname — the machine reports a managed
asset name (`DEL-02-0481-DT`), so a hostname test silently falls through to the
relay path.

Direct mode:
1. Backs up `nexus_gateway/` and `config.yaml` on the target to `*.bak-<timestamp>`
2. `rsync`s `nexus_gateway/`, `tests/` and `requirements.txt` to `~/nexus-italia-v2/`
3. Installs into `/opt/nexus-gateway-v2/` via `sudo`
4. Runs the test suite **on the target**, against the deployed venv

Relay mode packages with `tar` (so `rsync` is not needed on the calling machine),
stages to `~/deploy-staging/` on the relay, then rsyncs onward to the gateway.

The service is **not restarted automatically** — restart manually after verifying the deploy:

```bash
ssh iw2ohx2 sudo systemctl restart nexus-gateway-v2          # from macmini
ssh macmini-lan 'ssh iw2ohx2 sudo systemctl restart nexus-gateway-v2'   # via relay
```

### Tests

```bash
ssh iw2ohx2 'cd /opt/nexus-gateway-v2 && ./.venv/bin/python -m unittest discover -s tests -v'
```

`tests/test_recovery.py` is a regression suite for the 2026-08-23 silent-outage
defects (32 tests, stdlib `unittest` — no pytest dependency). It must be run
against the deployment venv, which provides the real `meshcore` library and
`yaml`; macmini's system Python has neither.

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

Reboot detection tracks the last known uptime as "unknown" (`None`) until a reading succeeds — it is never seeded with `0`. A `0` sentinel makes the `uptime < previous` test permanently false once a bad reading lands, which is what turned a recoverable event into a 46-hour outage in v2.1.2 (see *Failure detection and recovery*).

### 6b. Failure detection and recovery (v2.2.0)

The Companion's USB-serial device can **re-enumerate** — a brownout or firmware watchdog reset makes the kernel drop and re-add it (`usb 1-1: USB disconnect` followed by a fresh `cp210x converter now attached to ttyUSB0`). The previously open file descriptor is dead and never heals; only reopening the port recovers. The udev symlink (`/dev/meshcore-nexus`) is stable across re-enumeration, so the configured path stays valid.

Before v2.2.0 the gateway did not survive this. It kept running, kept MQTT connected, and kept logging `beacon transmitted` every three hours into a closed port — silently off the air from 2026-08-23 14:47 to 2026-08-25 12:47 CEST.

Three defects combined to hide it, all fixed in v2.2.0:

| # | Defect | Fix |
|---|--------|-----|
| 1 | TX/config wrappers logged success without inspecting the returned `Event`. The meshcore library **never raises** on a dead transport — `send()` returns `Event(EventType.ERROR, {"reason": "timeout"})`. | Every command result passes through `_check()`, raising `CompanionCommandError`. |
| 2 | `get_uptime()` fell back to `0`. `{"reason": "timeout"}` is a dict, so `data.get("uptime", 0)` silently yielded `0`. | Raises `CompanionCommandError`; never returns a fallback. |
| 3 | Reboot detection was `uptime < last_uptime` with `last_uptime` seeded at `0`, so once poisoned, `0 < 0` was false forever and the check never fired again. | Sentinel is `None`; a failed read never overwrites the last good value. |

Two detectors now run:

**Health loop** (primary) — reads `get_stats_core()` every `heartbeat_interval_sec`. After `companion_fail_threshold` consecutive unreadable polls it reopens the port and re-applies all configuration. Detects a detached companion in roughly `companion_fail_threshold × heartbeat_interval_sec` (~90 s at defaults).

**RX watchdog** (backstop) — covers the case the health loop cannot see: serial answers commands normally but no frames arrive, i.e. a wedged or deaf radio. Only inbound traffic proves the receiver works, so RX silence is the trigger. Any received frame counts, including public-channel traffic the gateway goes on to discard.

```yaml
runtime:
  rx_watchdog_enabled: true
  rx_watchdog_warn_sec: 7200        # 2 h  -> WARNING
  rx_watchdog_reconnect_sec: 28800  # 8 h  -> reconnect
  companion_fail_threshold: 3       # unreadable polls before recovery
  reconnect_cooldown_sec: 300       # minimum spacing between attempts
```

**The reconnect threshold must stay generous.** Measured RX inter-arrival gaps on the Bollate gateway across 7843 frames (2026-07-05 → 2026-08-23): median 121 s, p95 2168 s, p99 6098 s, **max 24381 s (6.8 h)**. Legitimate overnight lulls last hours. A 3600 s threshold would have fired 177 spurious reconnects (~3.6/day) over that window; 28800 s produced zero false positives. Detection latency is acceptable because the real-world failure (detached USB) is caught by the health loop in ~90 s, not here.

Recovery is rate-limited by `reconnect_cooldown_sec` so a persistently dead companion retries slowly instead of cycling the port. On reconnect, the Companion replays messages buffered during the outage — expect a burst carrying **old** `sender_timestamp` values; it is backlog, not a live traffic spike.

#### Diagnosing a silent outage

`systemctl status` is useless here — the process stays `active (running)` throughout. Fastest checks first:

```bash
# 1. Does anything hold the serial port? Empty output = detached companion.
sudo fuser -v /dev/ttyUSB0

# 2. Confirm from the process side — healthy shows "N -> /dev/ttyUSB0",
#    a zombie has only sockets and /dev/null.
sudo ls -l /proc/$(systemctl show -p MainPID --value nexus-gateway-v2)/fd | grep -i tty

# 3. RX is the only honest liveness signal. Zero frames for hours = off air,
#    regardless of what the "transmitted" lines claim.
sudo journalctl -u nexus-gateway-v2 --since "1 hour ago" \
  | grep -c "raw channel message received"

# 4. True outage start: USB disconnect / re-attach in the kernel log.
sudo dmesg -T | grep -i ttyUSB
```

**Timing fingerprint:** on a dead transport, consecutive init log lines sit **15–30 s apart** (library command timeouts). A healthy init completes in **milliseconds**. Wide gaps in the startup sequence mean commands are timing out silently.

The heartbeat now reports measured health rather than a hardcoded `"online"`: `status` (`online`/`degraded`), `companion_connected`, `last_rx_age_sec`, `companion_fail_count`, and `reconnect_count`. `status` is gated on the reconnect threshold, not the warn threshold, so routine quiet periods do not make it flap; consumers wanting a tighter policy should read `last_rx_age_sec` directly.

> **Monitoring:** any external probe must assert **recent RX** (or serial-fd presence). A probe that only checks whether the service is active would have reported green for the entire 46-hour outage.

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

### 8. Periodic advert (0hop, flood, and default-scope flood)

The gateway can periodically send advert commands to announce the Companion on the MeshCore network:

- **advert (0hop)** — local announcement, not propagated. Default: every **1 hour**.
- **floodadv (flood)** — announcement propagated at channel scope. Default: every **3 hours**.
- **default-scope floodadv** — flood advert explicitly sent at `default_scope` (broader reach). Default: every **3 hours**.

The default-scope flood advert re-asserts `default_scope` on the Companion before each transmission, then sends a flood advert. This ensures the Companion is visible to a wider area independently of the per-channel scope used for message relay. For example, with `channel_scope: "it-lom-mi"` and `default_scope: "it"`, Nexus messages stay within Lombardia/Milan while the gateway announces itself across all Italy.

Configurable parameters in `config.yaml` under `runtime`:

```yaml
runtime:
  advert_enabled: true                     # enable 0hop advert
  advert_interval_sec: 3600                # interval in seconds (default: 1 hour)
  flood_advert_enabled: true               # enable flood advert (channel scope)
  flood_advert_interval_sec: 10800         # interval in seconds (default: 3 hours)
  default_scope_advert_enabled: true       # enable flood advert with default_scope
  default_scope_advert_interval_sec: 10800 # interval in seconds (default: 3 hours)
```

All adverts are also sent once at service startup (+15s, +20s, +25s respectively).

### 9. Default flood scope configuration

Starting from **meshcore >= 2.3.7**, the gateway also sets a **default flood scope** on the Companion at startup via `set_default_flood_scope()`. This is distinct from the per-channel flood scope set by `set_flood_scope()` (feature 1 above): the default scope applies as the device-level fallback for any channel that does not have an explicit scope configured.

The default scope is controlled by the optional `default_scope` parameter in `config.yaml`. If omitted, it falls back to `channel_scope`:

```yaml
channel_scope: "it-lom-mi"   # scope for Nexus channel messages
default_scope: "it"           # optional — scope for device-level adverts (broader reach)
```

This separation allows a gateway to relay Nexus traffic with a tight regional scope while advertising its presence to a wider area. For example:
- `channel_scope: "it-lom-mi"` — Nexus messages stay within Lombardia/Milan
- `default_scope: "it"` — Companion adverts propagate across all Italy

If `default_scope` is not set, it defaults to the same value as `channel_scope` (backward compatible with existing deployments).

The default flood scope is applied:
- At gateway startup, after the channel scope is set
- After a Companion reboot is detected (same re-apply sequence as all other settings)

This feature requires `meshcore >= 2.3.7` and `meshcore-cli >= 1.5.7`. (Credit: Armando Accardo IK2XYP)

> **Known limitation — firmware may reject `SET_DEFAULT_FLOOD_SCOPE`.**
> On the Bollate companion (Heltec V3) this command returns an immediate
> `ERROR` (~9 ms, so a firmware rejection rather than a timeout). Because
> v2.1.x logged success without checking the result, the failure was invisible
> and **the default flood scope was almost certainly never actually applied**
> on this node. v2.2.0 surfaces it as
> `default flood scope refused, advertising without it`.
>
> The scope-set is deliberately **best-effort**: on refusal the flood advert is
> still transmitted, matching the previous effective behaviour rather than
> dropping the 3-hourly advert. The success line carries `scope_applied:
> true|false` so the distinction is visible in the log. Losing the wider scope
> reduces advert reach; it does not stop the gateway.
>
> `set_time` behaves the same way on this firmware and is treated the same way
> (`companion clock sync refused, continuing`) — a refused clock sync is no
> reason to keep the gateway off the air. Both are worth revisiting against a
> newer companion firmware build.

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
default_scope: "it"           # optional — defaults to channel_scope if omitted
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
  default_scope_advert_enabled: true
  default_scope_advert_interval_sec: 10800
```

---

## Software versioning

The gateway includes a software version number (`__version__` in `nexus_gateway/__init__.py`), separate from the MQTT `protocol_version` defined in `config.yaml`.

Both values are included in heartbeat payloads:

- `protocol_version` — MQTT message format version (from config, e.g. `"1.0"`)
- `software_version` — gateway software release (from code, e.g. `"2.2.0"`)

This allows tracking which software version is deployed on each gateway node.

---

## Operational notes

The install script adds the service user to the `dialout` group for serial port access.
After installation, if the Companion is not immediately detected by the service, a Raspberry Pi reboot may help.

### If the gateway looks alive but nobody hears it

Do not trust `systemctl status`, and do not trust `beacon transmitted` lines from
v2.1.x or earlier — both stayed reassuring through a 46-hour outage. Go straight to
`sudo fuser -v /dev/ttyUSB0` and the RX frame count. See
*Failure detection and recovery* for the full playbook.

On v2.2.0 the gateway recovers from a re-enumerated companion on its own (~90 s).
If it does not, check that `/dev/meshcore-nexus` still resolves — a udev rule that
no longer matches leaves the configured path dangling, and reconnection cannot
succeed until the symlink is restored.

### Companions on this host

Two MeshCore companions are separated by udev symlink, so they never contend:

| Symlink | Device | Used by |
|---------|--------|---------|
| `/dev/meshcore-nexus` | ttyUSB0 (CP2102) | `nexus-gateway-v2.service` |
| `/dev/meshcore-remoteterm` | ttyUSB1 (CP2102) | `remoteterm.service` |

Always reference the symlink, never `ttyUSB0` directly — the `ttyUSB*` numbering
is assignment order and can move across re-enumeration.

# NEXUS-ITALIA Gateway step-by-step installation

## 1. MQTT broker preparation

Before installing the gateway, the broker must have:

- An MQTT user matching the `gateway_id`
- ACLs consistent with the topics:
  - `nexus/v1/uplink`
  - `nexus/v1/downlink/<gateway_id>`
  - `nexus/v1/heartbeat/<gateway_id>`
  - `nexus/v1/status/<gateway_id>`

## 2. Connect the USB Companion

Verify the system detects it:

```bash
ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

## 3. Run the installer

```bash
sudo bash install_gateway.sh
```

## 4. Answer the prompts

The main fields are:

- `gateway_id`: for example `NEXUS-ITALIA-RM`
- `site_name`: site description
- `channel_name`: for example `NEXUS`
- `channel_number`: for example `1`
- `mqtt_host`: broker IP or hostname
- `mqtt_username`: usually the same as `gateway_id`

## 5. Verify the service

```bash
sudo systemctl status nexus-gateway --no-pager
journalctl -u nexus-gateway -f
```

## 6. Verify traffic on the broker side

On the broker/router server:

```bash
mosquitto_sub -h 127.0.0.1 -p 1883 -u router -P 'ROUTER_PASSWORD' -t 'nexus/v1/#' -v
```

## 7. Local radio test

The gateway uses a persistent serial connection to the Companion. To verify messages are flowing, check the service logs:

```bash
journalctl -u nexus-gateway -f | grep "uplink published"
```

Send a test message from another MeshCore node on the NEXUS channel and verify it appears in the logs and on the MQTT broker.

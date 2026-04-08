from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass
class MeshCliConfig:
    command: str
    serial_port: str
    baudrate: int
    timeout_sec: int
    mode: str = "serial"


@dataclass
class MqttConfig:
    host: str
    port: int
    username: str
    password: str
    keepalive: int
    tls: bool
    uplink_topic: str
    downlink_topic: str
    heartbeat_topic: str
    status_topic: str


@dataclass
class RuntimeConfig:
    dedupe_ttl_sec: int
    heartbeat_interval_sec: int
    poll_interval_sec: int
    log_level: str
    beacon_interval_sec: int = 10800
    beacon_channel: int = 2
    beacon_text: str = ""
    advert_interval_sec: int = 3600
    advert_enabled: bool = False
    flood_advert_interval_sec: int = 10800
    flood_advert_enabled: bool = False


@dataclass
class GatewayConfig:
    gateway_id: str
    site_name: str
    region: str
    mesh_id: str
    radio_band: str
    channel_name: str
    channel_number: int
    channel_scope: str
    protocol_version: str
    meshcli: MeshCliConfig
    mqtt: MqttConfig
    runtime: RuntimeConfig


def load_config(path: str | Path) -> GatewayConfig:
    data = yaml.safe_load(Path(path).read_text())
    return GatewayConfig(
        gateway_id=data["gateway_id"],
        site_name=data["site_name"],
        region=data["region"],
        mesh_id=data["mesh_id"],
        radio_band=str(data["radio_band"]),
        channel_name=data["channel_name"],
        channel_number=int(data["channel_number"]),
        channel_scope=str(data.get("channel_scope", "it-lo")),
        protocol_version=str(data["protocol_version"]),
        meshcli=MeshCliConfig(**data["meshcli"]),
        mqtt=MqttConfig(**data["mqtt"]),
        runtime=RuntimeConfig(
            dedupe_ttl_sec=data["runtime"]["dedupe_ttl_sec"],
            heartbeat_interval_sec=data["runtime"]["heartbeat_interval_sec"],
            poll_interval_sec=data["runtime"]["poll_interval_sec"],
            log_level=data["runtime"]["log_level"],
            beacon_interval_sec=int(data["runtime"].get("beacon_interval_sec", 10800)),
            beacon_channel=int(data["runtime"].get("beacon_channel", 2)),
            beacon_text=str(data["runtime"].get("beacon_text", "")),
            advert_interval_sec=int(data["runtime"].get("advert_interval_sec", 3600)),
            advert_enabled=bool(data["runtime"].get("advert_enabled", False)),
            flood_advert_interval_sec=int(data["runtime"].get("flood_advert_interval_sec", 10800)),
            flood_advert_enabled=bool(data["runtime"].get("flood_advert_enabled", False)),
        ),
    )

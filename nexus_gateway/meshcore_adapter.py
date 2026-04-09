from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from meshcore import MeshCore
from meshcore.events import EventType

from .config import GatewayConfig

logger = logging.getLogger("nexus_gateway.meshcore")


class MeshCoreAdapter:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self._mc: Optional[MeshCore] = None
        self._msg_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._subscription = None

    @property
    def is_connected(self) -> bool:
        return self._mc is not None and self._mc.is_connected

    async def connect(self) -> None:
        logger.info(
            "connecting to companion",
            extra={"extra": {
                "port": self.config.meshcore.serial_port,
                "baudrate": self.config.meshcore.baudrate,
            }},
        )
        self._mc = await MeshCore.create_serial(
            self.config.meshcore.serial_port,
            baudrate=self.config.meshcore.baudrate,
        )
        if self._mc is None:
            raise ConnectionError(
                f"failed to connect to companion on {self.config.meshcore.serial_port}"
            )
        self._subscription = self._mc.subscribe(
            EventType.CHANNEL_MSG_RECV, self._on_channel_message
        )
        await self._mc.start_auto_message_fetching()
        logger.info("companion connected, auto-fetch started")

    async def disconnect(self) -> None:
        if self._mc is not None:
            if self._subscription is not None:
                self._subscription.unsubscribe()
                self._subscription = None
            try:
                await self._mc.stop_auto_message_fetching()
            except Exception:
                pass
            await self._mc.disconnect()
            self._mc = None
            logger.info("companion disconnected")

    async def _on_channel_message(self, event: Any) -> None:
        payload = event.payload if hasattr(event, "payload") else {}
        if isinstance(payload, str):
            payload = {"text": payload}
        # Filter: only relay messages from the configured Nexus channel
        msg_chan = payload.get("channel") or payload.get("chan")
        if msg_chan is not None and int(msg_chan) != self.config.channel_number:
            logger.debug(
                "ignoring message from non-nexus channel",
                extra={"extra": {
                    "msg_channel": msg_chan,
                    "nexus_channel": self.config.channel_number,
                }},
            )
            return
        await self._msg_queue.put(payload)

    async def get_pending_messages(self) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        while not self._msg_queue.empty():
            try:
                messages.append(self._msg_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

    async def send_channel_message(self, text: str) -> None:
        assert self._mc is not None
        await self._mc.commands.send_chan_msg(
            chan=self.config.channel_number, msg=text
        )

    async def send_beacon(self, channel: int, text: str) -> None:
        assert self._mc is not None
        await self._mc.commands.send_chan_msg(chan=channel, msg=text)
        logger.info(
            "beacon transmitted",
            extra={"extra": {"text": text, "channel": channel}},
        )

    async def send_advert(self) -> None:
        assert self._mc is not None
        await self._mc.commands.send_advert(flood=False)
        logger.info("advert 0hop transmitted")

    async def send_flood_advert(self) -> None:
        assert self._mc is not None
        await self._mc.commands.send_advert(flood=True)
        logger.info("flood advert transmitted")

    async def get_uptime(self) -> int:
        assert self._mc is not None
        result = await self._mc.commands.get_stats_core()
        data = result.payload if hasattr(result, "payload") else {}
        if isinstance(data, dict):
            return int(data.get("uptime_secs", data.get("uptime", 0)))
        return 0

    async def sync_clock(self) -> None:
        assert self._mc is not None
        await self._mc.commands.set_time()
        logger.info("companion clock synced")

    async def set_scope(self, scope: str) -> None:
        assert self._mc is not None
        await self._mc.commands.set_flood_scope(scope)
        logger.info("channel scope set", extra={"extra": {"scope": scope}})

    async def get_channels(self) -> List[Dict[str, Any]]:
        assert self._mc is not None
        channels: List[Dict[str, Any]] = []
        for i in range(8):
            try:
                result = await self._mc.commands.get_channel(channel_idx=i)
                if hasattr(result, "payload") and result.payload:
                    channels.append(result.payload)
            except Exception:
                break
        return channels

    def normalize_messages(
        self, raw_messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in raw_messages:
            payload = self._extract_payload(item)
            if not payload:
                continue
            sender = str(
                item.get("from")
                or item.get("sender")
                or item.get("pubkey_prefix")
                or item.get("sender_id")
                or "unknown"
            )
            msg_id = str(
                item.get("msg_id")
                or item.get("id")
                or self._build_msg_id(sender, payload)
            )
            normalized.append(
                {
                    "msg_id": msg_id,
                    "protocol_version": self.config.protocol_version,
                    "direction": "uplink",
                    "origin_gateway_id": self.config.gateway_id,
                    "origin_site_name": self.config.site_name,
                    "origin_region": self.config.region,
                    "origin_mesh_id": self.config.mesh_id,
                    "radio_band": self.config.radio_band,
                    "channel": self.config.channel_name,
                    "sender_mesh_node": sender,
                    "timestamp_utc": self._timestamp(item),
                    "payload_type": "text",
                    "payload": payload,
                    "payload_hash": hashlib.sha256(payload.encode()).hexdigest(),
                }
            )
        return normalized

    def _extract_payload(self, item: Dict[str, Any]) -> str:
        for key in ("text", "payload", "msg", "message", "body"):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""

    def _timestamp(self, item: Dict[str, Any]) -> str:
        for key in ("timestamp_utc", "timestamp", "ts"):
            val = item.get(key)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, (int, float)) and val > 0:
                return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
        return datetime.now(timezone.utc).isoformat()

    def _build_msg_id(self, sender: str, payload: str) -> str:
        bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        base = f"{self.config.gateway_id}|{sender}|{self.config.channel_number}|{payload}|{bucket}"
        return hashlib.sha256(base.encode()).hexdigest()

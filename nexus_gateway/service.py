from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timezone

from . import __version__
from .config import GatewayConfig
from .dedupe import TTLCache
from .meshcore_adapter import MeshCoreAdapter
from .mqtt_client import GatewayMqttClient

logger = logging.getLogger("nexus_gateway.service")


class GatewayService:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.meshcore = MeshCoreAdapter(config)
        self.dedupe = TTLCache(config.runtime.dedupe_ttl_sec)
        self.stop_event = asyncio.Event()
        self.mqtt = GatewayMqttClient(config.mqtt, self._schedule_downlink)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_companion_uptime: int = 0

    async def start(self) -> None:
        logger.info(
            "gateway service starting",
            extra={"extra": {"gateway_id": self.config.gateway_id}},
        )
        self._loop = asyncio.get_running_loop()
        self._install_signal_handlers()

        await self.meshcore.connect()
        await self.meshcore.sync_clock()
        await self._ensure_nexus_channel()
        await self._configure_scope()
        await self.meshcore.set_path_hash_mode(self.config.path_hash_mode)

        self.mqtt.connect()
        self.publish_status("online")

        tasks = [
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            asyncio.create_task(self._message_consumer_loop(), name="msg_consumer"),
            asyncio.create_task(self._companion_health_loop(), name="companion_health"),
        ]
        if self.config.runtime.beacon_text:
            tasks.append(asyncio.create_task(self._beacon_loop(), name="beacon"))
        if self.config.runtime.advert_enabled:
            tasks.append(asyncio.create_task(self._advert_loop(), name="advert"))
            logger.info(
                "advert 0hop enabled",
                extra={"extra": {"interval_sec": self.config.runtime.advert_interval_sec}},
            )
        if self.config.runtime.flood_advert_enabled:
            tasks.append(
                asyncio.create_task(self._flood_advert_loop(), name="flood_advert")
            )
            logger.info(
                "flood advert enabled",
                extra={"extra": {"interval_sec": self.config.runtime.flood_advert_interval_sec}},
            )

        logger.info("gateway service started")
        await self.stop_event.wait()

        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        self.publish_status("offline")
        self.mqtt.disconnect()
        await self.meshcore.disconnect()
        logger.info("gateway service stopped")

    def _install_signal_handlers(self) -> None:
        assert self._loop is not None
        try:
            self._loop.add_signal_handler(signal.SIGTERM, self._request_shutdown)
            self._loop.add_signal_handler(signal.SIGINT, self._request_shutdown)
        except NotImplementedError:
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)

    def _request_shutdown(self) -> None:
        logger.info("shutdown requested")
        self.stop_event.set()

    def _signal_handler(self, signum: int, frame: object) -> None:
        logger.info("shutdown requested", extra={"extra": {"signal": signum}})
        self.stop_event.set()

    async def _ensure_nexus_channel(self) -> None:
        if not self.config.channel_secret:
            return
        try:
            await self.meshcore.ensure_channel(
                self.config.channel_number,
                self.config.channel_name,
                self.config.channel_secret,
            )
        except Exception as exc:
            logger.exception(
                "failed to ensure nexus channel on companion",
                extra={"extra": {"error": str(exc)}},
            )

    async def _configure_scope(self) -> None:
        scope = self.config.channel_scope
        try:
            await self.meshcore.set_scope(scope)
            logger.info(
                "channel scope configured", extra={"extra": {"scope": scope}}
            )
        except Exception as exc:
            logger.exception(
                "failed to set channel scope",
                extra={"extra": {"error": str(exc), "scope": scope}},
            )

    async def _wait_or_stop(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _message_consumer_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                raw = await self.meshcore.get_pending_messages()
                if raw:
                    normalized = self.meshcore.normalize_messages(raw)
                    for msg in normalized:
                        msg_id = msg["msg_id"]
                        if self.dedupe.seen(msg_id):
                            continue
                        self.dedupe.add(msg_id)
                        self.mqtt.publish_json(
                            self.config.mqtt.uplink_topic, msg
                        )
                        logger.info(
                            "uplink published",
                            extra={"extra": {
                                "msg_id": msg_id,
                                "channel": self.config.channel_name,
                            }},
                        )
            except Exception as exc:
                logger.exception(
                    "message consumer failed",
                    extra={"extra": {"error": str(exc)}},
                )
            await self._wait_or_stop(self.config.runtime.poll_interval_sec)

    async def _companion_health_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                uptime = await self.meshcore.get_uptime()
                if uptime < self._last_companion_uptime:
                    logger.warning(
                        "companion reboot detected, re-applying scope",
                        extra={"extra": {
                            "prev_uptime": self._last_companion_uptime,
                            "new_uptime": uptime,
                        }},
                    )
                    await self.meshcore.sync_clock()
                    await self._ensure_nexus_channel()
                    await self._configure_scope()
                self._last_companion_uptime = uptime
            except Exception as exc:
                logger.warning(
                    "companion health check failed",
                    extra={"extra": {"error": str(exc)}},
                )
            await self._wait_or_stop(self.config.runtime.heartbeat_interval_sec)

    async def _heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            self.publish_heartbeat()
            await self._wait_or_stop(self.config.runtime.heartbeat_interval_sec)

    async def _beacon_loop(self) -> None:
        await self._wait_or_stop(10)
        while not self.stop_event.is_set():
            try:
                await self.meshcore.send_beacon(
                    self.config.runtime.beacon_channel,
                    self.config.runtime.beacon_text,
                )
            except Exception as exc:
                logger.exception(
                    "beacon transmit failed",
                    extra={"extra": {"error": str(exc)}},
                )
            await self._wait_or_stop(self.config.runtime.beacon_interval_sec)

    async def _advert_loop(self) -> None:
        await self._wait_or_stop(15)
        while not self.stop_event.is_set():
            try:
                await self.meshcore.send_advert()
            except Exception as exc:
                logger.exception(
                    "advert 0hop failed",
                    extra={"extra": {"error": str(exc)}},
                )
            await self._wait_or_stop(self.config.runtime.advert_interval_sec)

    async def _flood_advert_loop(self) -> None:
        await self._wait_or_stop(20)
        while not self.stop_event.is_set():
            try:
                await self.meshcore.send_flood_advert()
            except Exception as exc:
                logger.exception(
                    "flood advert failed",
                    extra={"extra": {"error": str(exc)}},
                )
            await self._wait_or_stop(self.config.runtime.flood_advert_interval_sec)

    def _schedule_downlink(self, payload: dict) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future,
                self._handle_downlink(payload),
            )

    async def _handle_downlink(self, payload: dict) -> None:
        msg_id = str(payload.get("msg_id") or "")
        if msg_id and self.dedupe.seen(msg_id):
            logger.info(
                "downlink ignored duplicate",
                extra={"extra": {"msg_id": msg_id}},
            )
            return
        text = str(payload.get("payload") or "").strip()
        if not text:
            logger.warning("downlink ignored empty payload")
            return
        try:
            await self.meshcore.send_channel_message(text)
            if msg_id:
                self.dedupe.add(msg_id)
            logger.info(
                "downlink transmitted",
                extra={"extra": {
                    "msg_id": msg_id,
                    "channel_number": self.config.channel_number,
                }},
            )
        except Exception as exc:
            logger.exception(
                "downlink transmit failed",
                extra={"extra": {"error": str(exc), "msg_id": msg_id}},
            )

    def publish_heartbeat(self) -> None:
        payload = {
            "gateway_id": self.config.gateway_id,
            "site_name": self.config.site_name,
            "region": self.config.region,
            "radio_band": self.config.radio_band,
            "status": "online",
            "serial_port": self.config.meshcore.serial_port,
            "last_seen_utc": datetime.now(timezone.utc).isoformat(),
            "protocol_version": self.config.protocol_version,
            "software_version": __version__,
        }
        self.mqtt.publish_json(self.config.mqtt.heartbeat_topic, payload)

    def publish_status(self, status: str) -> None:
        payload = {
            "gateway_id": self.config.gateway_id,
            "status": status,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        self.mqtt.publish_json(self.config.mqtt.status_topic, payload)

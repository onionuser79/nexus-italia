from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import datetime, timezone

from . import __version__
from .config import GatewayConfig
from .dedupe import TTLCache
from .meshcore_adapter import CompanionCommandError, MeshCoreAdapter
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
        # None = "not yet known". Must not default to 0: a 0 makes the
        # `uptime < last` reboot test permanently false once an unreadable
        # companion has reported 0 even one time.
        self._last_companion_uptime: int | None = None
        self._companion_fail_count: int = 0
        self._last_reconnect_monotonic: float | None = None
        self._reconnect_count: int = 0

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
        await self._configure_default_scope()
        await self.meshcore.set_path_hash_mode(self.config.path_hash_mode)

        self.mqtt.connect()
        self.publish_status("online")

        tasks = [
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            asyncio.create_task(self._message_consumer_loop(), name="msg_consumer"),
            asyncio.create_task(self._companion_health_loop(), name="companion_health"),
        ]
        if self.config.runtime.rx_watchdog_enabled:
            tasks.append(
                asyncio.create_task(self._rx_watchdog_loop(), name="rx_watchdog")
            )
            logger.info(
                "rx watchdog enabled",
                extra={"extra": {
                    "warn_sec": self.config.runtime.rx_watchdog_warn_sec,
                    "reconnect_sec": self.config.runtime.rx_watchdog_reconnect_sec,
                }},
            )
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
        if self.config.runtime.default_scope_advert_enabled:
            tasks.append(
                asyncio.create_task(self._default_scope_advert_loop(), name="default_scope_advert")
            )
            logger.info(
                "default scope flood advert enabled",
                extra={"extra": {
                    "scope": self.config.default_scope,
                    "interval_sec": self.config.runtime.default_scope_advert_interval_sec,
                }},
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

    async def _configure_default_scope(self) -> None:
        scope = self.config.default_scope
        try:
            await self.meshcore.set_default_scope(scope)
            logger.info(
                "default flood scope configured", extra={"extra": {"scope": scope}}
            )
        except Exception as exc:
            logger.exception(
                "failed to set default flood scope",
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

    async def _reapply_companion_config(self) -> None:
        await self.meshcore.sync_clock()
        await self._ensure_nexus_channel()
        await self._configure_scope()
        await self._configure_default_scope()
        await self.meshcore.set_path_hash_mode(self.config.path_hash_mode)

    async def _recover_companion(self, reason: str) -> bool:
        """Reopen the serial transport and re-apply config. Returns True on success.

        Rate-limited by reconnect_cooldown_sec so a persistently dead companion
        produces a slow retry, not a tight cycle of port open/close.
        """
        now = time.monotonic()
        if (
            self._last_reconnect_monotonic is not None
            and now - self._last_reconnect_monotonic
            < self.config.runtime.reconnect_cooldown_sec
        ):
            logger.debug(
                "companion recovery suppressed by cooldown",
                extra={"extra": {
                    "reason": reason,
                    "since_last_sec": round(now - self._last_reconnect_monotonic, 1),
                    "cooldown_sec": self.config.runtime.reconnect_cooldown_sec,
                }},
            )
            return False

        self._last_reconnect_monotonic = now
        self._reconnect_count += 1
        logger.warning(
            "companion recovery starting",
            extra={"extra": {
                "reason": reason,
                "attempt": self._reconnect_count,
            }},
        )
        try:
            await self.meshcore.reconnect()
            await self._reapply_companion_config()
        except Exception as exc:
            logger.exception(
                "companion recovery failed",
                extra={"extra": {"reason": reason, "error": str(exc)}},
            )
            return False

        # Clear the failure state only once the port is genuinely back.
        self._companion_fail_count = 0
        self._last_companion_uptime = None
        logger.warning(
            "companion recovery completed",
            extra={"extra": {"reason": reason, "attempt": self._reconnect_count}},
        )
        return True

    async def _companion_health_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                uptime = await self.meshcore.get_uptime()
                self._companion_fail_count = 0
                if (
                    self._last_companion_uptime is not None
                    and uptime < self._last_companion_uptime
                ):
                    logger.warning(
                        "companion reboot detected, re-applying scope",
                        extra={"extra": {
                            "prev_uptime": self._last_companion_uptime,
                            "new_uptime": uptime,
                        }},
                    )
                    await self._reapply_companion_config()
                self._last_companion_uptime = uptime
            except CompanionCommandError as exc:
                self._companion_fail_count += 1
                logger.warning(
                    "companion unreadable",
                    extra={"extra": {
                        "error": str(exc),
                        "consecutive_failures": self._companion_fail_count,
                        "threshold": self.config.runtime.companion_fail_threshold,
                    }},
                )
                if (
                    self._companion_fail_count
                    >= self.config.runtime.companion_fail_threshold
                ):
                    await self._recover_companion("companion unreadable")
            except Exception as exc:
                logger.exception(
                    "companion health check failed",
                    extra={"extra": {"error": str(exc)}},
                )
            await self._wait_or_stop(self.config.runtime.heartbeat_interval_sec)

    async def _rx_watchdog_loop(self) -> None:
        """Reconnect when the radio has gone deaf.

        Covers the case the health loop cannot see: the serial link answers
        commands normally (so uptime reads fine) but no frames arrive, which is
        what a detached or wedged radio looks like from userspace. Only inbound
        traffic proves the receiver works, so RX silence is the trigger.
        """
        warn_sec = self.config.runtime.rx_watchdog_warn_sec
        reconnect_sec = self.config.runtime.rx_watchdog_reconnect_sec
        warned = False
        while not self.stop_event.is_set():
            age = self.meshcore.rx_age_sec
            if age is not None:
                if age >= reconnect_sec:
                    logger.error(
                        "rx silence exceeded reconnect threshold, recovering",
                        extra={"extra": {
                            "rx_age_sec": round(age),
                            "reconnect_sec": reconnect_sec,
                        }},
                    )
                    if await self._recover_companion("rx silence"):
                        # reconnect() restarts the RX clock, so the next
                        # evaluation measures the fresh link.
                        warned = False
                elif age >= warn_sec:
                    if not warned:
                        logger.warning(
                            "rx silence detected",
                            extra={"extra": {
                                "rx_age_sec": round(age),
                                "warn_sec": warn_sec,
                                "reconnect_sec": reconnect_sec,
                            }},
                        )
                        warned = True
                else:
                    warned = False
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

    async def _default_scope_advert_loop(self) -> None:
        await self._wait_or_stop(25)
        while not self.stop_event.is_set():
            try:
                await self.meshcore.send_default_scope_flood_advert(self.config.default_scope)
            except Exception as exc:
                logger.exception(
                    "default scope flood advert failed",
                    extra={"extra": {"error": str(exc), "scope": self.config.default_scope}},
                )
            await self._wait_or_stop(self.config.runtime.default_scope_advert_interval_sec)

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
        # Report measured health, not a constant. A hardcoded "online" made the
        # heartbeat useless for spotting an off-air gateway: it kept asserting
        # health for the whole of a 46h silent outage (2026-08-23).
        rx_age = self.meshcore.rx_age_sec
        connected = self.meshcore.is_connected
        # Gate on the reconnect threshold, not the warn threshold: measured RX
        # gaps reach ~6.8 h in normal operation, so warn-level silence is
        # routine and would make "degraded" flap. Consumers wanting a tighter
        # policy can read last_rx_age_sec directly.
        degraded = (
            not connected
            or self._companion_fail_count > 0
            or (
                rx_age is not None
                and rx_age >= self.config.runtime.rx_watchdog_reconnect_sec
            )
        )
        payload = {
            "gateway_id": self.config.gateway_id,
            "site_name": self.config.site_name,
            "region": self.config.region,
            "radio_band": self.config.radio_band,
            "status": "degraded" if degraded else "online",
            "companion_connected": connected,
            "last_rx_age_sec": None if rx_age is None else round(rx_age),
            "companion_fail_count": self._companion_fail_count,
            "reconnect_count": self._reconnect_count,
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

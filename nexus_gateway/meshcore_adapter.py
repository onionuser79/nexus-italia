from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from meshcore import MeshCore
from meshcore.events import EventType

from .config import GatewayConfig

logger = logging.getLogger("nexus_gateway.meshcore")


class CompanionCommandError(RuntimeError):
    """A companion command did not succeed.

    The meshcore library never raises on a dead transport: `send()` returns
    `Event(EventType.ERROR, {"reason": "timeout"})`. Every command wrapper in
    this module funnels that case through `_check()` into this exception, so a
    silently-detached USB companion becomes a hard error instead of a phantom
    success. See `_check()` for the rationale.
    """


class MeshCoreAdapter:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self._mc: Optional[MeshCore] = None
        self._msg_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._subscription = None
        self._last_rx_monotonic: Optional[float] = None

    @property
    def is_connected(self) -> bool:
        return self._mc is not None and self._mc.is_connected

    @property
    def rx_age_sec(self) -> Optional[float]:
        """Seconds since the last frame received from the radio.

        Returns None when never connected. RX is the only trustworthy liveness
        signal: TX commands can be accepted by the library while the radio is
        detached, but an inbound frame proves the receiver is genuinely alive.
        """
        if self._last_rx_monotonic is None:
            return None
        return time.monotonic() - self._last_rx_monotonic

    def mark_rx(self) -> None:
        self._last_rx_monotonic = time.monotonic()

    @staticmethod
    def _check(result: Any, what: str) -> Any:
        """Raise CompanionCommandError if `result` is an explicit error event.

        Only an explicit `EventType.ERROR` is treated as failure. A result that
        is None or carries no `.type` is accepted, so this stays tolerant of
        library commands that do not return an Event while still catching the
        real-world failure mode (timeout / no_event_received on a dead port).
        """
        if result is not None and getattr(result, "type", None) == EventType.ERROR:
            payload = getattr(result, "payload", None)
            reason = ""
            if isinstance(payload, dict):
                reason = str(payload.get("reason") or payload.get("error") or "")
            raise CompanionCommandError(
                f"{what} failed: {reason or 'error response from companion'}"
            )
        return result

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
        # Start the RX clock now so the watchdog measures from connect time
        # rather than firing immediately on a quiet mesh.
        self.mark_rx()
        logger.info("companion connected, auto-fetch started")

    async def reconnect(self) -> None:
        """Tear down and reopen the serial transport.

        Needed because the companion's USB-serial device re-enumerates (e.g.
        after a brownout or watchdog reset). The old file descriptor is dead and
        never heals; only reopening `serial_port` recovers. The udev symlink is
        stable across re-enumeration, so the configured path stays valid.
        """
        logger.warning(
            "reconnecting to companion",
            extra={"extra": {"port": self.config.meshcore.serial_port}},
        )
        try:
            await self.disconnect()
        except Exception as exc:
            logger.warning(
                "companion teardown during reconnect failed, continuing",
                extra={"extra": {"error": str(exc)}},
            )
        finally:
            # disconnect() clears these on success; force them so a partial
            # teardown cannot leave a stale handle behind.
            self._mc = None
            self._subscription = None
        await self.connect()

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
        # Record RX before any filtering: a frame on ANY channel (including the
        # public channel 0 we go on to discard) proves the receiver is alive,
        # which is exactly what the RX watchdog needs to know.
        self.mark_rx()
        raw = event.payload if hasattr(event, "payload") else {}
        logger.debug(
            "raw channel message received",
            extra={"extra": {"raw_payload": raw, "raw_type": type(raw).__name__}},
        )
        if isinstance(raw, str):
            raw = {"text": raw}
        # Filter: only relay messages from the configured Nexus channel
        # Use explicit None checks — channel_idx 0 (Public) is falsy but valid
        msg_chan = raw.get("channel_idx")
        if msg_chan is None:
            msg_chan = raw.get("channel")
        if msg_chan is None:
            msg_chan = raw.get("chan")
        if msg_chan is None or int(msg_chan) != self.config.channel_number:
            logger.debug(
                "ignoring message from non-nexus channel",
                extra={"extra": {
                    "msg_channel": msg_chan,
                    "nexus_channel": self.config.channel_number,
                }},
            )
            return
        await self._msg_queue.put(raw)

    async def get_pending_messages(self) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        while not self._msg_queue.empty():
            try:
                messages.append(self._msg_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

    async def ensure_channel(
        self, channel_idx: int, name: str, secret_hex: str
    ) -> None:
        """Check that the Nexus channel exists on the companion; create it if not."""
        self._require_connection()
        try:
            result = await self._mc.commands.get_channel(channel_idx=channel_idx)
            info = result.payload if hasattr(result, "payload") else {}
            existing_name = ""
            if isinstance(info, dict):
                existing_name = str(
                    info.get("name") or info.get("channel_name") or ""
                ).strip().rstrip("\x00")
            if existing_name and existing_name.upper() == name.upper():
                logger.info(
                    "nexus channel already present on companion",
                    extra={"extra": {
                        "channel_idx": channel_idx,
                        "name": existing_name,
                    }},
                )
                return
        except Exception as exc:
            logger.warning(
                "could not read channel from companion, will attempt creation",
                extra={"extra": {"channel_idx": channel_idx, "error": str(exc)}},
            )

        secret_bytes = bytes.fromhex(secret_hex)
        self._check(
            await self._mc.commands.set_channel(channel_idx, name, secret_bytes),
            "set_channel",
        )
        logger.info(
            "nexus channel created on companion",
            extra={"extra": {"channel_idx": channel_idx, "name": name}},
        )

    def _require_connection(self) -> MeshCore:
        if self._mc is None:
            raise CompanionCommandError("no companion connection")
        return self._mc

    async def send_channel_message(self, text: str) -> None:
        mc = self._require_connection()
        self._check(
            await mc.commands.send_chan_msg(
                chan=self.config.channel_number, msg=text
            ),
            "send_chan_msg",
        )

    async def send_beacon(self, channel: int, text: str) -> None:
        mc = self._require_connection()
        self._check(
            await mc.commands.send_chan_msg(chan=channel, msg=text), "send_beacon"
        )
        logger.info(
            "beacon transmitted",
            extra={"extra": {"text": text, "channel": channel}},
        )

    async def send_advert(self) -> None:
        mc = self._require_connection()
        self._check(await mc.commands.send_advert(flood=False), "send_advert")
        logger.info("advert 0hop transmitted")

    async def send_flood_advert(self) -> None:
        mc = self._require_connection()
        self._check(await mc.commands.send_advert(flood=True), "send_flood_advert")
        logger.info("flood advert transmitted")

    async def send_default_scope_flood_advert(self, scope: str) -> None:
        """Flood-advert under the default scope.

        The scope-set is best-effort on purpose: some companion firmware
        rejects SET_DEFAULT_FLOOD_SCOPE outright. Losing the wider scope is a
        reduction in reach, not a reason to skip the advert entirely — so a
        refusal is logged and the advert still goes out. `scope_applied` in the
        success line records which of the two actually happened.
        """
        mc = self._require_connection()
        scope_applied = True
        try:
            self._check(
                await mc.commands.set_default_flood_scope(scope),
                "set_default_flood_scope",
            )
        except CompanionCommandError as exc:
            scope_applied = False
            logger.warning(
                "default flood scope refused, advertising without it",
                extra={"extra": {"scope": scope, "error": str(exc)}},
            )
        self._check(await mc.commands.send_advert(flood=True), "send_flood_advert")
        logger.info(
            "default scope flood advert transmitted",
            extra={"extra": {"scope": scope, "scope_applied": scope_applied}},
        )

    async def get_uptime(self) -> int:
        """Return companion uptime in seconds.

        Raises CompanionCommandError if the companion cannot be read. It must
        never return a fallback 0: a 0 is indistinguishable from a genuine
        reboot, and pinning the caller's last-seen uptime at 0 permanently
        defeats the `uptime < last` reboot test.
        """
        mc = self._require_connection()
        result = self._check(await mc.commands.get_stats_core(), "get_stats_core")
        data = getattr(result, "payload", None)
        if not isinstance(data, dict):
            raise CompanionCommandError(
                f"get_stats_core returned no payload (type={type(data).__name__})"
            )
        raw = data.get("uptime_secs", data.get("uptime"))
        if raw is None:
            raise CompanionCommandError(
                "get_stats_core payload carried no uptime field"
            )
        return int(raw)

    async def sync_clock(self) -> bool:
        """Best-effort companion clock sync. Returns True if it succeeded.

        Deliberately non-fatal: the firmware can reject set_time (observed
        returning a bare ERROR with no reason, e.g. when it declines to move an
        already-good clock), and a refused clock sync is no reason to keep the
        gateway off the air. Liveness is decided by get_uptime() and by whether
        the serial port opens, not by this.
        """
        mc = self._require_connection()
        try:
            self._check(await mc.commands.set_time(int(time.time())), "set_time")
        except CompanionCommandError as exc:
            logger.warning(
                "companion clock sync refused, continuing",
                extra={"extra": {"error": str(exc)}},
            )
            return False
        logger.info("companion clock synced")
        return True

    async def set_scope(self, scope: str) -> None:
        mc = self._require_connection()
        self._check(await mc.commands.set_flood_scope(scope), "set_flood_scope")
        logger.info("channel scope set", extra={"extra": {"scope": scope}})

    async def set_default_scope(self, scope: str) -> None:
        mc = self._require_connection()
        self._check(
            await mc.commands.set_default_flood_scope(scope),
            "set_default_flood_scope",
        )
        logger.info("default flood scope set", extra={"extra": {"scope": scope}})

    async def set_path_hash_mode(self, mode: int = 1) -> None:
        mc = self._require_connection()
        self._check(await mc.commands.set_path_hash_mode(mode), "set_path_hash_mode")
        logger.info("path hash mode set", extra={"extra": {"mode": mode}})

    async def get_channels(self) -> List[Dict[str, Any]]:
        mc = self._require_connection()
        channels: List[Dict[str, Any]] = []
        for i in range(8):
            try:
                result = await mc.commands.get_channel(channel_idx=i)
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

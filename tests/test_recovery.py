"""Regression tests for the silent off-air failure of 2026-08-23.

Background: the companion's USB-serial device re-enumerated. The gateway lost
the port, never reopened it, and stayed "healthy" for 46 hours while logging
phantom "beacon transmitted" lines. Three defects combined:

  1. TX/config wrappers logged success without checking the returned Event.
     The meshcore library does not raise on a dead transport — `send()` returns
     `Event(EventType.ERROR, {"reason": "timeout"})`.
  2. `get_uptime()` fell back to 0 on an unreadable companion.
  3. Reboot detection was `uptime < last_uptime`, so once `last_uptime` was
     pinned at 0 the test could never fire again.

Run against the deployment venv (has the real meshcore library):
    /opt/nexus-gateway-v2/.venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meshcore.events import Event, EventType  # noqa: E402

from nexus_gateway.config import (  # noqa: E402
    GatewayConfig,
    MeshCoreConfig,
    MqttConfig,
    RuntimeConfig,
)
from nexus_gateway.meshcore_adapter import (  # noqa: E402
    CompanionCommandError,
    MeshCoreAdapter,
)
from nexus_gateway.service import GatewayService  # noqa: E402

TIMEOUT_EVENT = Event(EventType.ERROR, {"reason": "timeout"})


def make_config(**runtime_overrides: Any) -> GatewayConfig:
    runtime_kwargs = dict(
        dedupe_ttl_sec=180,
        heartbeat_interval_sec=30,
        poll_interval_sec=5,
        log_level="DEBUG",
        beacon_interval_sec=10800,
        beacon_channel=1,
        beacon_text="TEST BEACON",
        rx_watchdog_enabled=True,
        rx_watchdog_warn_sec=900,
        rx_watchdog_reconnect_sec=3600,
        companion_fail_threshold=3,
        reconnect_cooldown_sec=300,
    )
    runtime_kwargs.update(runtime_overrides)
    return GatewayConfig(
        gateway_id="NEXUS-TEST",
        site_name="Test",
        region="lombardia",
        mesh_id="test",
        radio_band="868",
        channel_name="Nexus",
        channel_number=1,
        channel_scope="it-lom-mi",
        default_scope="it",
        channel_secret="00" * 16,
        path_hash_mode=1,
        protocol_version="1.0",
        meshcore=MeshCoreConfig(serial_port="/dev/null", baudrate=115200),
        mqtt=MqttConfig(
            host="localhost",
            port=1883,
            username="u",
            password="p",
            keepalive=30,
            tls=False,
            uplink_topic="up",
            downlink_topic="down",
            heartbeat_topic="hb",
            status_topic="st",
        ),
        runtime=RuntimeConfig(**runtime_kwargs),
    )


class FakeCommands:
    """Stands in for meshcore's CommandHandler, returning canned Events."""

    def __init__(self, result: Any = None) -> None:
        self.result = result if result is not None else Event(EventType.OK, {})
        self.calls: List[str] = []

    async def _respond(self, name: str) -> Any:
        self.calls.append(name)
        return self.result

    async def get_stats_core(self) -> Any:
        return await self._respond("get_stats_core")

    async def send_chan_msg(self, chan: int, msg: str) -> Any:
        return await self._respond("send_chan_msg")

    async def send_advert(self, flood: bool = False) -> Any:
        return await self._respond("send_advert")

    async def set_time(self, ts: int) -> Any:
        return await self._respond("set_time")

    async def set_flood_scope(self, scope: str) -> Any:
        return await self._respond("set_flood_scope")

    async def set_default_flood_scope(self, scope: str) -> Any:
        return await self._respond("set_default_flood_scope")

    async def set_path_hash_mode(self, mode: int) -> Any:
        return await self._respond("set_path_hash_mode")


def make_adapter(result: Any = None) -> MeshCoreAdapter:
    adapter = MeshCoreAdapter(make_config())
    adapter._mc = SimpleNamespace(commands=FakeCommands(result), is_connected=True)
    return adapter


class TestGetUptime(unittest.IsolatedAsyncioTestCase):
    async def test_raises_on_timeout_instead_of_returning_zero(self) -> None:
        """Core regression: a dead port used to yield uptime 0 silently.

        `{"reason": "timeout"}` is a dict, so the old
        `int(data.get("uptime_secs", data.get("uptime", 0)))` returned 0.
        """
        adapter = make_adapter(TIMEOUT_EVENT)
        with self.assertRaises(CompanionCommandError) as ctx:
            await adapter.get_uptime()
        self.assertIn("timeout", str(ctx.exception))

    async def test_returns_real_uptime(self) -> None:
        adapter = make_adapter(Event(EventType.STATS_CORE, {"uptime_secs": 4234958}))
        self.assertEqual(await adapter.get_uptime(), 4234958)

    async def test_raises_when_payload_has_no_uptime_field(self) -> None:
        adapter = make_adapter(Event(EventType.STATS_CORE, {"batt": 4100}))
        with self.assertRaises(CompanionCommandError):
            await adapter.get_uptime()

    async def test_raises_when_disconnected(self) -> None:
        adapter = MeshCoreAdapter(make_config())
        with self.assertRaises(CompanionCommandError):
            await adapter.get_uptime()


class TestPhantomTransmit(unittest.IsolatedAsyncioTestCase):
    """TX must fail loudly rather than log 'transmitted' into a dead port."""

    async def test_beacon_raises_on_error_event(self) -> None:
        adapter = make_adapter(TIMEOUT_EVENT)
        with self.assertRaises(CompanionCommandError):
            await adapter.send_beacon(1, "TEST BEACON")

    async def test_beacon_succeeds_on_ok(self) -> None:
        adapter = make_adapter(Event(EventType.OK, {}))
        await adapter.send_beacon(1, "TEST BEACON")
        self.assertIn("send_chan_msg", adapter._mc.commands.calls)

    async def test_advert_raises_on_error_event(self) -> None:
        adapter = make_adapter(TIMEOUT_EVENT)
        with self.assertRaises(CompanionCommandError):
            await adapter.send_advert()

    async def test_downlink_raises_on_error_event(self) -> None:
        adapter = make_adapter(TIMEOUT_EVENT)
        with self.assertRaises(CompanionCommandError):
            await adapter.send_channel_message("hello")

    async def test_scope_commands_raise_on_error_event(self) -> None:
        adapter = make_adapter(TIMEOUT_EVENT)
        for coro in (
            adapter.set_scope("it-lom-mi"),
            adapter.set_default_scope("it"),
            adapter.set_path_hash_mode(1),
        ):
            with self.assertRaises(CompanionCommandError):
                await coro


class TestBestEffortPaths(unittest.IsolatedAsyncioTestCase):
    """Two commands are deliberately non-fatal; keep them that way."""

    async def test_clock_sync_refusal_is_not_fatal(self) -> None:
        """Firmware can reject set_time; that must not stop the gateway."""
        adapter = make_adapter(TIMEOUT_EVENT)
        self.assertFalse(await adapter.sync_clock())

    async def test_clock_sync_reports_success(self) -> None:
        adapter = make_adapter(Event(EventType.OK, {}))
        self.assertTrue(await adapter.sync_clock())

    async def test_advert_still_sent_when_default_scope_refused(self) -> None:
        """Regression guard: a refused default scope must not suppress the advert.

        Observed live — this firmware rejects SET_DEFAULT_FLOOD_SCOPE. Aborting
        here would silently drop the 3-hourly flood advert entirely.
        """

        class ScopeRefusingCommands(FakeCommands):
            async def set_default_flood_scope(self, scope: str) -> Any:
                self.calls.append("set_default_flood_scope")
                return TIMEOUT_EVENT

        adapter = MeshCoreAdapter(make_config())
        cmds = ScopeRefusingCommands(Event(EventType.OK, {}))
        adapter._mc = SimpleNamespace(commands=cmds, is_connected=True)

        await adapter.send_default_scope_flood_advert("it")

        self.assertIn("set_default_flood_scope", cmds.calls)
        self.assertIn("send_advert", cmds.calls)

    async def test_advert_failure_still_propagates(self) -> None:
        """The advert itself failing is a real error and must raise."""
        adapter = make_adapter(TIMEOUT_EVENT)
        with self.assertRaises(CompanionCommandError):
            await adapter.send_default_scope_flood_advert("it")

    async def test_non_event_return_is_tolerated(self) -> None:
        """Only an explicit ERROR is a failure; a None return must not raise."""
        adapter = make_adapter(None)
        adapter._mc.commands.result = None
        await adapter.send_advert()


class TestRxTracking(unittest.IsolatedAsyncioTestCase):
    async def test_public_channel_message_still_marks_rx(self) -> None:
        """A discarded channel-0 frame still proves the receiver works."""
        adapter = make_adapter()
        self.assertIsNone(adapter.rx_age_sec)
        await adapter._on_channel_message(
            SimpleNamespace(payload={"channel_idx": 0, "text": "public traffic"})
        )
        self.assertIsNotNone(adapter.rx_age_sec)
        # channel 0 != configured channel 1, so nothing was queued for uplink
        self.assertEqual(await adapter.get_pending_messages(), [])

    async def test_nexus_channel_message_marks_rx_and_queues(self) -> None:
        adapter = make_adapter()
        await adapter._on_channel_message(
            SimpleNamespace(payload={"channel_idx": 1, "text": "nexus traffic"})
        )
        self.assertIsNotNone(adapter.rx_age_sec)
        self.assertEqual(len(await adapter.get_pending_messages()), 1)


class FakeAdapter:
    """Adapter double for driving GatewayService loops."""

    def __init__(
        self,
        uptimes: Optional[List[Any]] = None,
        rx_age: Optional[float] = 0.0,
    ) -> None:
        self._uptimes = list(uptimes or [])
        self._rx_age = rx_age
        self.reconnect_calls = 0
        self.is_connected = True
        self.reapplied: List[str] = []

    @property
    def rx_age_sec(self) -> Optional[float]:
        return self._rx_age

    async def get_uptime(self) -> int:
        if not self._uptimes:
            raise CompanionCommandError("exhausted")
        value = self._uptimes.pop(0)
        if isinstance(value, Exception):
            raise value
        return int(value)

    async def reconnect(self) -> None:
        self.reconnect_calls += 1
        self._rx_age = 0.0

    # Full config surface, so _reapply_companion_config genuinely completes
    # instead of dying on a missing attribute and masking the result.
    async def sync_clock(self) -> None:
        self.reapplied.append("sync_clock")

    async def ensure_channel(self, channel_idx: int, name: str, secret: str) -> None:
        self.reapplied.append("ensure_channel")

    async def set_scope(self, scope: str) -> None:
        self.reapplied.append("set_scope")

    async def set_default_scope(self, scope: str) -> None:
        self.reapplied.append("set_default_scope")

    async def set_path_hash_mode(self, mode: int) -> None:
        self.reapplied.append("set_path_hash_mode")


def make_service(adapter: FakeAdapter, **runtime_overrides: Any) -> GatewayService:
    service = GatewayService(make_config(**runtime_overrides))
    service.meshcore = adapter  # type: ignore[assignment]
    service.mqtt = SimpleNamespace(  # type: ignore[assignment]
        publish_json=lambda *a, **k: None,
        connect=lambda: None,
        disconnect=lambda: None,
    )
    return service


def drive(service: GatewayService, ticks: int) -> None:
    """Let a service loop run `ticks` times, then trip the stop event."""
    remaining = {"n": ticks}

    async def fake_wait(_seconds: float) -> bool:
        remaining["n"] -= 1
        if remaining["n"] <= 0:
            service.stop_event.set()
            return True
        return False

    service._wait_or_stop = fake_wait  # type: ignore[assignment]


class TestRebootDetection(unittest.IsolatedAsyncioTestCase):
    async def test_reboot_detected_after_unreadable_period(self) -> None:
        """The `0 < 0` trap: reboot detection must survive a dead-link gap.

        Sequence mirrors the real outage: healthy uptime, the companion becomes
        unreadable, then it comes back with a low uptime. The old code recorded
        0 during the gap, so `10 < 0` was false and the reboot was missed.
        """
        adapter = FakeAdapter(
            uptimes=[
                4234958,
                CompanionCommandError("timeout"),
                CompanionCommandError("timeout"),
                CompanionCommandError("timeout"),
                10,
            ]
        )
        service = make_service(adapter)
        reapplied: List[int] = []

        async def track_reapply() -> None:
            reapplied.append(1)

        service._reapply_companion_config = track_reapply  # type: ignore[assignment]
        drive(service, 6)
        await service._companion_health_loop()

        self.assertEqual(
            adapter.reconnect_calls, 1, "3 consecutive failures must trigger recovery"
        )
        self.assertTrue(reapplied, "returning companion must have config re-applied")

    async def test_steady_uptime_never_triggers_recovery(self) -> None:
        adapter = FakeAdapter(uptimes=[100, 130, 160, 190])
        service = make_service(adapter)
        drive(service, 5)
        await service._companion_health_loop()
        self.assertEqual(adapter.reconnect_calls, 0)

    async def test_failed_read_does_not_poison_last_uptime(self) -> None:
        """A failed read must leave the last good uptime intact.

        This is what made the outage permanent: the old code stored the
        fallback 0, after which `uptime < 0` could never be true again. Here a
        transient failure sits between two good reads; the drop from 5000 to
        4000 must still register as a reboot.
        """
        adapter = FakeAdapter(
            uptimes=[5000, CompanionCommandError("timeout"), 4000]
        )
        # threshold above the single failure so no reconnect interferes
        service = make_service(adapter, companion_fail_threshold=5)
        reapplied: List[int] = []

        async def track_reapply() -> None:
            reapplied.append(1)

        service._reapply_companion_config = track_reapply  # type: ignore[assignment]
        drive(service, 4)
        await service._companion_health_loop()

        self.assertEqual(adapter.reconnect_calls, 0)
        self.assertEqual(
            len(reapplied), 1, "drop from 5000 to 4000 must be seen as a reboot"
        )

    async def test_last_uptime_starts_unknown(self) -> None:
        """Contract: the sentinel is None, never 0."""
        service = make_service(FakeAdapter())
        self.assertIsNone(service._last_companion_uptime)

    async def test_first_reading_is_not_a_reboot(self) -> None:
        """A fresh start has no previous uptime; it must not claim a reboot."""
        adapter = FakeAdapter(uptimes=[500])
        service = make_service(adapter)
        reapplied: List[int] = []

        async def track_reapply() -> None:
            reapplied.append(1)

        service._reapply_companion_config = track_reapply  # type: ignore[assignment]
        drive(service, 2)
        await service._companion_health_loop()
        self.assertEqual(reapplied, [])


class TestRxWatchdog(unittest.IsolatedAsyncioTestCase):
    async def test_reconnects_after_rx_silence(self) -> None:
        adapter = FakeAdapter(rx_age=4000.0)
        service = make_service(adapter)
        drive(service, 2)
        await service._rx_watchdog_loop()
        self.assertEqual(adapter.reconnect_calls, 1)
        # Recovery must run to completion: port reopened AND config re-applied.
        self.assertEqual(
            adapter.reapplied,
            [
                "sync_clock",
                "ensure_channel",
                "set_scope",
                "set_default_scope",
                "set_path_hash_mode",
            ],
        )

    async def test_quiet_link_within_threshold_is_left_alone(self) -> None:
        adapter = FakeAdapter(rx_age=1200.0)  # past warn, below reconnect
        service = make_service(adapter)
        drive(service, 3)
        await service._rx_watchdog_loop()
        self.assertEqual(adapter.reconnect_calls, 0)

    async def test_default_threshold_tolerates_measured_quiet_periods(self) -> None:
        """Guard the empirically-derived default against being tightened.

        Measured max legitimate RX gap on the live gateway was 24381 s (6.8 h).
        A default at or below that reintroduces spurious reconnects — 3600 s
        would have produced 177 of them over the same 7-week window.
        """
        defaults = RuntimeConfig(
            dedupe_ttl_sec=180,
            heartbeat_interval_sec=30,
            poll_interval_sec=5,
            log_level="INFO",
        )
        self.assertGreater(
            defaults.rx_watchdog_reconnect_sec,
            24381,
            "reconnect threshold must exceed the longest observed quiet period",
        )

    async def test_long_but_legitimate_quiet_period_is_left_alone(self) -> None:
        """A 6.8 h lull is normal traffic, not a fault."""
        adapter = FakeAdapter(rx_age=24381.0)
        service = make_service(
            adapter, rx_watchdog_warn_sec=7200, rx_watchdog_reconnect_sec=28800
        )
        drive(service, 3)
        await service._rx_watchdog_loop()
        self.assertEqual(adapter.reconnect_calls, 0)

    async def test_never_connected_does_not_reconnect(self) -> None:
        adapter = FakeAdapter(rx_age=None)
        service = make_service(adapter)
        drive(service, 3)
        await service._rx_watchdog_loop()
        self.assertEqual(adapter.reconnect_calls, 0)

    async def test_cooldown_suppresses_reconnect_storm(self) -> None:
        adapter = FakeAdapter(rx_age=4000.0)
        service = make_service(adapter)

        # rx_age stays high even after reconnect, so without a cooldown the
        # watchdog would recover on every single tick.
        async def reconnect_without_clearing() -> None:
            adapter.reconnect_calls += 1

        adapter.reconnect = reconnect_without_clearing  # type: ignore[assignment]
        drive(service, 5)
        await service._rx_watchdog_loop()
        self.assertEqual(
            adapter.reconnect_calls, 1, "cooldown must permit only one attempt"
        )


class TestOutageEndToEnd(unittest.IsolatedAsyncioTestCase):
    """Reproduces the 2026-08-23 outage through the real adapter.

    The transport is dead (every command times out) but the process is
    otherwise fine. The old build read this as uptime 0, logged nothing
    alarming, and sat off-air for 46 hours. It must now escalate to recovery.
    """

    async def test_dead_transport_escalates_to_recovery(self) -> None:
        adapter = MeshCoreAdapter(make_config())
        adapter._mc = SimpleNamespace(
            commands=FakeCommands(TIMEOUT_EVENT), is_connected=True
        )
        service = GatewayService(make_config(companion_fail_threshold=2))
        service.meshcore = adapter  # type: ignore[assignment]
        service.mqtt = SimpleNamespace(  # type: ignore[assignment]
            publish_json=lambda *a, **k: None
        )
        recovered: List[str] = []

        async def fake_recover(reason: str) -> bool:
            recovered.append(reason)
            return True

        service._recover_companion = fake_recover  # type: ignore[assignment]
        drive(service, 4)
        await service._companion_health_loop()

        self.assertTrue(
            recovered, "a timing-out transport must escalate to recovery, not idle"
        )

    async def test_healthy_transport_never_escalates(self) -> None:
        adapter = MeshCoreAdapter(make_config())
        adapter._mc = SimpleNamespace(
            commands=FakeCommands(Event(EventType.STATS_CORE, {"uptime_secs": 900})),
            is_connected=True,
        )
        service = GatewayService(make_config(companion_fail_threshold=2))
        service.meshcore = adapter  # type: ignore[assignment]
        service.mqtt = SimpleNamespace(  # type: ignore[assignment]
            publish_json=lambda *a, **k: None
        )
        recovered: List[str] = []

        async def fake_recover(reason: str) -> bool:
            recovered.append(reason)
            return True

        service._recover_companion = fake_recover  # type: ignore[assignment]
        drive(service, 4)
        await service._companion_health_loop()

        self.assertEqual(recovered, [])


class TestHeartbeatHonesty(unittest.IsolatedAsyncioTestCase):
    def _capture(self, service: GatewayService) -> dict:
        captured: dict = {}
        service.mqtt = SimpleNamespace(  # type: ignore[assignment]
            publish_json=lambda topic, payload: captured.update(payload)
        )
        service.publish_heartbeat()
        return captured

    async def test_reports_online_when_healthy(self) -> None:
        service = make_service(FakeAdapter(rx_age=5.0))
        payload = self._capture(service)
        self.assertEqual(payload["status"], "online")
        self.assertEqual(payload["last_rx_age_sec"], 5)

    async def test_reports_degraded_during_rx_silence(self) -> None:
        """The old heartbeat hardcoded "online" for the whole 46h outage."""
        service = make_service(FakeAdapter(rx_age=4000.0))
        payload = self._capture(service)
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["last_rx_age_sec"], 4000)

    async def test_reports_degraded_when_companion_disconnected(self) -> None:
        adapter = FakeAdapter(rx_age=5.0)
        adapter.is_connected = False
        service = make_service(adapter)
        payload = self._capture(service)
        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["companion_connected"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

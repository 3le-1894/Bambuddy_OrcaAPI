"""Regression tests for cached reads, refresh deadlines and task lifecycle."""

import asyncio
import unittest
from unittest.mock import patch

from backend.app.services.polling_printer_adapter import PollingPrinterAdapter
from backend.app.services.printer_adapter import (
    FleetPrinterStatus,
    PrinterActivity,
    PrinterCapabilities,
    PrinterFamily,
    PrinterReadiness,
)
from backend.app.services.printer_manager import PrinterManager


def sample(filename="part.gcode"):
    return FleetPrinterStatus(
        connected=True,
        activity=PrinterActivity.IDLE,
        readiness=PrinterReadiness.READY,
        native_state="standby",
        filename=filename,
        last_seen_at=1234,
    )


class FakePollingAdapter(PollingPrinterAdapter):
    family = PrinterFamily.KLIPPER
    capabilities = PrinterCapabilities()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.outcomes = asyncio.Queue()
        self.calls = 0
        self.cancelled_fetches = 0

    async def fetch_status(self):
        self.calls += 1
        try:
            outcome = await self.outcomes.get()
        except asyncio.CancelledError:
            self.cancelled_fetches += 1
            raise
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def pause(self):
        return False

    async def resume(self):
        return False

    async def cancel(self):
        return False


async def wait_until(condition):
    async def wait():
        while not condition():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=1)


class TestPollingAdapter(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapters = []

    async def asyncTearDown(self):
        for adapter in self.adapters:
            await adapter.stop()

    def adapter(self, **kwargs):
        adapter = FakePollingAdapter(**kwargs)
        self.adapters.append(adapter)
        return adapter

    async def test_reads_do_not_connect_or_refresh(self):
        adapter = self.adapter()
        for _ in range(50):
            self.assertFalse(adapter.get_status().connected)
        self.assertEqual(adapter.calls, 0)

    async def test_hung_refresh_does_not_block_reads_or_event_loop(self):
        adapter = self.adapter()
        await adapter.start()
        await wait_until(lambda: adapter.calls == 1)
        for _ in range(50):
            self.assertFalse(adapter.get_status().connected)
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), timeout=0.1)
        self.assertEqual(adapter.calls, 1)

    async def test_start_is_immediate_and_idempotent(self):
        adapter = self.adapter()
        await asyncio.wait_for(adapter.start(), timeout=0.1)
        task = adapter._task
        await adapter.start()
        self.assertIs(adapter._task, task)
        await wait_until(lambda: adapter.calls == 1)

    async def test_success_populates_cache_without_refetch_on_read(self):
        adapter = self.adapter()
        adapter.outcomes.put_nowait(sample())
        await adapter.start()
        await wait_until(lambda: adapter.get_status().connected)
        for _ in range(50):
            self.assertEqual(adapter.get_status(), sample())
        self.assertEqual(adapter.calls, 1)

    async def test_stale_data_keeps_last_seen_and_filename_but_not_readiness(self):
        adapter = self.adapter(stale_after=10)
        adapter.outcomes.put_nowait(sample())
        await adapter.start()
        await wait_until(lambda: adapter.get_status().connected)
        with patch(
            "backend.app.services.polling_printer_adapter.time.monotonic", return_value=adapter._last_success_at + 11
        ):
            stale = adapter.get_status()
        self.assertFalse(stale.connected)
        self.assertEqual(stale.readiness, PrinterReadiness.OFFLINE)
        self.assertEqual(stale.filename, "part.gcode")
        self.assertEqual(stale.last_seen_at, 1234)
        self.assertEqual(adapter.calls, 1)

    async def test_failure_marks_previous_snapshot_offline(self):
        adapter = self.adapter(poll_interval=0.01)
        adapter.outcomes.put_nowait(sample())
        adapter.outcomes.put_nowait(RuntimeError("test failure"))
        await adapter.start()
        await wait_until(lambda: adapter.calls >= 2 and not adapter.get_status().connected)
        self.assertEqual(adapter.get_status().filename, "part.gcode")
        self.assertEqual(adapter.get_status().last_seen_at, 1234)

    async def test_deadline_cancels_fetch_and_retries_without_overlap(self):
        adapter = self.adapter(poll_interval=0.01, request_timeout=0.01)
        await adapter.start()
        await wait_until(lambda: adapter.cancelled_fetches >= 1)
        adapter.outcomes.put_nowait(sample())
        await wait_until(lambda: adapter.get_status().connected)
        self.assertGreaterEqual(adapter.calls, 2)

    async def test_backoff_is_capped_and_resets_after_success(self):
        adapter = self.adapter(poll_interval=0.01, max_backoff=0.02)
        for _ in range(3):
            adapter.outcomes.put_nowait(RuntimeError("test failure"))
        adapter.outcomes.put_nowait(sample())
        delays = []
        real_sleep = asyncio.sleep

        async def record_sleep(delay):
            if delay > 0:
                delays.append(delay)
            await real_sleep(delay)

        with patch("backend.app.services.polling_printer_adapter.asyncio.sleep", new=record_sleep):
            await adapter.start()
            await wait_until(lambda: len(delays) >= 4)
        self.assertEqual(delays[:4], [0.01, 0.02, 0.02, 0.01])

    async def test_stop_cancels_pending_fetch_and_restart_does_not_reuse_ready_cache(self):
        adapter = self.adapter(poll_interval=0.01)
        adapter.outcomes.put_nowait(sample())
        await adapter.start()
        await wait_until(lambda: adapter.calls >= 2)
        await adapter.stop()
        self.assertEqual(adapter.cancelled_fetches, 1)
        self.assertIsNone(adapter._task)
        self.assertFalse(adapter.get_status().connected)
        await adapter.start()
        self.assertFalse(adapter.get_status().connected)
        adapter.outcomes.put_nowait(sample("new.gcode"))
        await wait_until(lambda: adapter.get_status().connected)
        self.assertEqual(adapter.get_status().filename, "new.gcode")

    def test_invalid_intervals_rejected(self):
        for kwargs in (
            {"poll_interval": 0},
            {"request_timeout": float("inf")},
            {"stale_after": float("nan")},
            {"poll_interval": 5, "max_backoff": 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                FakePollingAdapter(**kwargs)


class TestManagedFleetAdapters(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = PrinterManager()

    async def asyncTearDown(self):
        await self.manager.shutdown_fleet_adapters()

    async def test_registration_refreshes_in_background_and_plate_gate_stays_local(self):
        adapter = FakePollingAdapter()
        adapter.outcomes.put_nowait(sample())
        await self.manager.register_fleet_adapter(5, adapter)
        await wait_until(lambda: adapter.get_status().connected)
        self.manager._awaiting_plate_clear.add(5)
        self.assertEqual(self.manager.get_fleet_status(5).readiness, PrinterReadiness.AWAITING_CLEARANCE)
        self.assertEqual(self.manager.get_all_fleet_statuses()[5].readiness, PrinterReadiness.AWAITING_CLEARANCE)
        self.assertEqual(adapter.calls, 1)
        self.assertNotIn(5, self.manager._clients)

    async def test_replacement_awaits_retired_task(self):
        old = FakePollingAdapter()
        await self.manager.register_fleet_adapter(5, old)
        await wait_until(lambda: old.calls == 1)
        new = FakePollingAdapter()
        await self.manager.register_fleet_adapter(5, new)
        self.assertEqual(old.cancelled_fetches, 1)
        self.assertIsNone(old._task)
        self.assertIs(self.manager.get_adapter(5), new)

    async def test_shutdown_stops_all_tasks(self):
        adapters = [FakePollingAdapter(), FakePollingAdapter()]
        for printer_id, adapter in enumerate(adapters):
            await self.manager.register_fleet_adapter(printer_id, adapter)
        await wait_until(lambda: all(adapter.calls == 1 for adapter in adapters))
        await self.manager.shutdown_fleet_adapters()
        self.assertEqual(self.manager.get_all_fleet_statuses(), {})
        self.assertTrue(all(adapter._task is None for adapter in adapters))

    async def test_bambu_id_collision_rejected(self):
        self.manager._clients[5] = object()
        adapter = FakePollingAdapter()
        with self.assertRaises(ValueError):
            await self.manager.register_fleet_adapter(5, adapter)
        self.assertIsNone(adapter._task)

    async def test_concurrent_replacements_do_not_leave_orphan_tasks(self):
        old = FakePollingAdapter()
        await self.manager.register_fleet_adapter(5, old)
        stop_entered = asyncio.Event()
        release_stop = asyncio.Event()
        original_stop = old.stop

        async def delayed_stop():
            stop_entered.set()
            await release_stop.wait()
            await original_stop()

        old.stop = delayed_stop
        first, second = FakePollingAdapter(), FakePollingAdapter()
        replacement = asyncio.create_task(self.manager.register_fleet_adapter(5, first))
        await asyncio.wait_for(stop_entered.wait(), timeout=1)
        concurrent = asyncio.create_task(self.manager.register_fleet_adapter(5, second))
        release_stop.set()
        await asyncio.gather(replacement, concurrent)
        self.assertIsNone(old._task)
        self.assertIsNone(first._task)
        self.assertIs(self.manager.get_adapter(5), second)

    async def test_failed_start_is_cleaned_up_and_not_registered(self):
        adapter = FakePollingAdapter()
        original_start = adapter.start

        async def failing_start():
            await original_start()
            raise RuntimeError("setup failed")

        adapter.start = failing_start
        with self.assertRaises(RuntimeError):
            await self.manager.register_fleet_adapter(5, adapter)
        self.assertIsNone(adapter._task)
        self.assertIsNone(self.manager.get_adapter(5))

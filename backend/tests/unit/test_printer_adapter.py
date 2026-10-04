"""Fleet contract regressions; no network connections or database sessions."""

import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

from backend.app.services.bambu_adapter import BambuPrinterAdapter
from backend.app.services.bambu_mqtt import BambuMQTTClient, PrinterState
from backend.app.services.printer_adapter import PrinterActivity, PrinterAdapter, PrinterReadiness
from backend.app.services.printer_manager import PrinterManager


def make_client(state="IDLE", connected=True):
    client = MagicMock(spec=BambuMQTTClient)
    client.state = PrinterState(state=state, connected=connected)
    client.last_seen_at = 1234.5
    client.is_stale.return_value = False
    return client


class TestBambuAdapter(unittest.IsolatedAsyncioTestCase):
    def test_activity_mapping(self):
        cases = {
            "IDLE": PrinterActivity.IDLE,
            "PREPARE": PrinterActivity.PREPARING,
            "SLICING": PrinterActivity.PREPARING,
            "RUNNING": PrinterActivity.PRINTING,
            "PAUSE": PrinterActivity.PAUSED,
            "FINISH": PrinterActivity.COMPLETED,
            "FAILED": PrinterActivity.ERROR,
            "new_firmware_state": PrinterActivity.UNKNOWN,
        }
        for native, activity in cases.items():
            with self.subTest(native=native):
                self.assertEqual(BambuPrinterAdapter(make_client(native)).get_status().activity, activity)

    def test_connection_does_not_imply_ready(self):
        for native, readiness in (
            ("unknown", PrinterReadiness.UNKNOWN),
            ("RUNNING", PrinterReadiness.BUSY),
            ("PAUSE", PrinterReadiness.BUSY),
            ("FAILED", PrinterReadiness.BLOCKED),
        ):
            with self.subTest(native=native):
                self.assertEqual(BambuPrinterAdapter(make_client(native)).get_status().readiness, readiness)

    def test_stale_snapshot_does_not_trigger_reconnect(self):
        client = make_client("RUNNING")
        client.is_stale.return_value = True
        status = BambuPrinterAdapter(client).get_status()
        self.assertEqual(status.activity, PrinterActivity.OFFLINE)
        self.assertEqual(status.readiness, PrinterReadiness.OFFLINE)
        self.assertEqual(status.native_state, "RUNNING")
        self.assertTrue(client.state.connected)
        client.check_staleness.assert_not_called()

    def test_units_and_filename(self):
        client = make_client("RUNNING")
        client.state.remaining_time = 17
        client.state.progress = 41.5
        client.state.gcode_file = "plate_1.gcode"
        status = BambuPrinterAdapter(client).get_status()
        self.assertEqual(status.remaining_seconds, 1020)
        self.assertEqual(status.progress_percent, 41.5)
        self.assertEqual(status.filename, "plate_1.gcode")
        self.assertEqual(status.last_seen_at, 1234.5)

    def test_temperatures_preserve_zero_and_missing(self):
        client = make_client()
        client.state.temperatures = {"nozzle": 0, "nozzle_2": 210, "nozzle_2_target": 220}
        status = BambuPrinterAdapter(client).get_status()
        self.assertEqual(len(status.tools), 2)
        self.assertEqual(status.tools[0].current, 0)
        self.assertIsNone(status.tools[0].target)
        self.assertEqual(status.tools[1].target, 220)
        self.assertIsNone(status.bed)

    def test_chamber_requires_real_sensor(self):
        client = make_client()
        client.state.temperatures = {"chamber": 40}
        self.assertIsNone(BambuPrinterAdapter(client).get_status().chamber)
        self.assertEqual(BambuPrinterAdapter(client, chamber_sensor=True).get_status().chamber.current, 40)

    def test_snapshot_is_detached_and_immutable(self):
        client = make_client()
        client.state.temperatures = {"nozzle": 25}
        status = BambuPrinterAdapter(client).get_status()
        client.state.temperatures["nozzle"] = 200
        self.assertEqual(status.tools[0].current, 25)
        with self.assertRaises(FrozenInstanceError):
            status.connected = False

    def test_adapter_satisfies_contract(self):
        self.assertIsInstance(BambuPrinterAdapter(make_client()), PrinterAdapter)

    async def test_controls_preserve_submission_result(self):
        client = make_client()
        adapter = BambuPrinterAdapter(client)
        for operation, method in (
            (adapter.pause, client.pause_print),
            (adapter.resume, client.resume_print),
            (adapter.cancel, client.stop_print),
        ):
            method.return_value = False
            self.assertFalse(await operation())
            method.return_value = True
            self.assertTrue(await operation())
            self.assertEqual(method.call_count, 2)


class TestFleetManager(unittest.TestCase):
    def setUp(self):
        self.manager = PrinterManager()
        self.client = make_client()
        self.manager._clients[1] = self.client
        self.manager._models[1] = "P1S"

    def test_unknown_printer(self):
        self.assertIsNone(self.manager.get_adapter(999))
        self.assertIsNone(self.manager.get_fleet_status(999))

    def test_plate_gate_preserves_activity_and_offline_state(self):
        self.manager._awaiting_plate_clear.add(1)
        status = self.manager.get_fleet_status(1)
        self.assertEqual(status.activity, PrinterActivity.IDLE)
        self.assertEqual(status.readiness, PrinterReadiness.AWAITING_CLEARANCE)
        self.client.state.connected = False
        self.assertEqual(self.manager.get_fleet_status(1).readiness, PrinterReadiness.OFFLINE)

    def test_reconnect_uses_current_client_and_legacy_status(self):
        replacement = make_client("RUNNING")
        self.manager._clients[1] = replacement
        self.assertIs(self.manager.get_status(1), replacement.state)
        self.assertIs(self.manager.get_client(1), replacement)
        self.assertEqual(self.manager.get_all_fleet_statuses()[1].activity, PrinterActivity.PRINTING)
        self.client.check_staleness.assert_not_called()

    def test_missing_last_seen(self):
        client = BambuMQTTClient.__new__(BambuMQTTClient)
        client._last_message_time = 0.0
        self.assertIsNone(client.last_seen_at)
        client._last_message_time = 123.0
        self.assertEqual(client.last_seen_at, 123.0)

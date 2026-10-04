"""Adapt the existing Bambu MQTT connection for fleet consumers."""

import asyncio
from typing import TYPE_CHECKING

from backend.app.services.printer_adapter import (
    FleetPrinterStatus,
    PrinterActivity,
    PrinterCapabilities,
    PrinterFamily,
    PrinterReadiness,
    TemperatureReading,
)

if TYPE_CHECKING:
    from backend.app.services.bambu_mqtt import BambuMQTTClient


_ACTIVITIES = {
    "IDLE": PrinterActivity.IDLE,
    "PREPARE": PrinterActivity.PREPARING,
    "SLICING": PrinterActivity.PREPARING,
    "RUNNING": PrinterActivity.PRINTING,
    "PAUSE": PrinterActivity.PAUSED,
    "FINISH": PrinterActivity.COMPLETED,
    "FAILED": PrinterActivity.ERROR,
}


class BambuPrinterAdapter:
    """Wrap a manager-owned client without creating a second connection."""

    family = PrinterFamily.BAMBU
    capabilities = PrinterCapabilities(pause=True, resume=True, cancel=True)

    def __init__(self, client: "BambuMQTTClient", *, chamber_sensor: bool = False):
        self._client = client
        self._chamber_sensor = chamber_sensor

    def get_status(self) -> FleetPrinterStatus:
        state = self._client.state
        # MQTT owns telemetry updates/reconnects. A fleet read must never
        # invoke check_staleness(), which may tear down and rebuild a session.
        connected = state.connected and not self._client.is_stale()
        activity = _ACTIVITIES.get(state.state, PrinterActivity.UNKNOWN)
        if not connected:
            activity = PrinterActivity.OFFLINE

        readiness = PrinterReadiness.UNKNOWN
        if activity == PrinterActivity.OFFLINE:
            readiness = PrinterReadiness.OFFLINE
        elif activity in (PrinterActivity.IDLE, PrinterActivity.COMPLETED):
            readiness = PrinterReadiness.READY
        elif activity == PrinterActivity.ERROR:
            readiness = PrinterReadiness.BLOCKED
        elif activity in (PrinterActivity.PREPARING, PrinterActivity.PRINTING, PrinterActivity.PAUSED):
            readiness = PrinterReadiness.BUSY

        temperatures = dict(state.temperatures)

        def reading(identifier: str) -> TemperatureReading | None:
            target_key = f"{identifier}_target"
            if identifier not in temperatures and target_key not in temperatures:
                return None
            return TemperatureReading(identifier, temperatures.get(identifier), temperatures.get(target_key))

        tools = tuple(value for name in ("nozzle", "nozzle_2") if (value := reading(name)) is not None)
        return FleetPrinterStatus(
            connected=connected,
            activity=activity,
            readiness=readiness,
            native_state=state.state,
            filename=state.gcode_file or state.current_print or state.subtask_name,
            progress_percent=max(0.0, min(100.0, state.progress)),
            remaining_seconds=max(0, state.remaining_time) * 60,
            last_seen_at=self._client.last_seen_at,
            tools=tools,
            bed=reading("bed"),
            chamber=reading("chamber") if self._chamber_sensor else None,
            error_codes=tuple(error.code for error in state.hms_errors),
        )

    async def pause(self) -> bool:
        return await asyncio.to_thread(self._client.pause_print)

    async def resume(self) -> bool:
        return await asyncio.to_thread(self._client.resume_print)

    async def cancel(self) -> bool:
        return await asyncio.to_thread(self._client.stop_print)

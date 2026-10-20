"""Moonraker monitoring and guarded print controls."""

import asyncio
import math
import re
import time

import httpx

from backend.app.services.polling_printer_adapter import PollingPrinterAdapter, offline_snapshot
from backend.app.services.printer_adapter import (
    FleetPrinterStatus,
    PrinterActivity,
    PrinterCapabilities,
    PrinterControlError,
    PrinterFamily,
    PrinterReadiness,
    TemperatureReading,
)


def number(value) -> float | None:
    """Missing, malformed and non-finite measurements are unavailable, not zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def normalize_status(status: dict, *, received_at: float) -> FleetPrinterStatus:
    stats = status.get("print_stats") or {}
    native = stats.get("state", "unknown")
    mapping = {
        "standby": (PrinterActivity.IDLE, PrinterReadiness.READY),
        "printing": (PrinterActivity.PRINTING, PrinterReadiness.BUSY),
        "paused": (PrinterActivity.PAUSED, PrinterReadiness.BUSY),
        "complete": (PrinterActivity.COMPLETED, PrinterReadiness.AWAITING_CLEARANCE),
        "cancelled": (PrinterActivity.IDLE, PrinterReadiness.BLOCKED),
        "error": (PrinterActivity.ERROR, PrinterReadiness.BLOCKED),
    }
    activity, readiness = mapping.get(native, (PrinterActivity.UNKNOWN, PrinterReadiness.UNKNOWN))
    webhooks = status.get("webhooks") or {}
    if webhooks.get("state") != "ready":
        native = webhooks.get("state", "unknown")
        activity = PrinterActivity.ERROR if native in ("error", "shutdown") else PrinterActivity.UNKNOWN
        readiness = PrinterReadiness.BLOCKED if activity == PrinterActivity.ERROR else PrinterReadiness.UNKNOWN
    progress = number((status.get("virtual_sdcard") or {}).get("progress"))
    # File progress is meaningful only for an active or completed job.
    if activity not in (PrinterActivity.PRINTING, PrinterActivity.PAUSED, PrinterActivity.COMPLETED):
        progress = None
    if progress is not None:
        progress = max(0.0, min(1.0, progress))
    duration = number(stats.get("print_duration"))
    remaining = None
    if activity == PrinterActivity.PRINTING and progress is not None and 0 < progress < 1 and duration and duration > 0:
        estimate = duration * (1 - progress) / progress
        if math.isfinite(estimate):
            remaining = round(estimate)
    elif activity == PrinterActivity.COMPLETED:
        remaining = 0

    def temperature(name: str) -> TemperatureReading:
        values = status[name]
        return TemperatureReading(name, number(values.get("temperature")), number(values.get("target")))

    tools = tuple(temperature(name) for name in sorted(status) if re.fullmatch(r"extruder\d*", name))
    # Use only explicitly named chamber sensors; never treat Pi/MCU temperature as chamber temperature.
    chamber_name = next(
        (name for name in ("temperature_sensor chamber", "heater_generic chamber") if name in status), None
    )
    filename = stats.get("filename")
    return FleetPrinterStatus(
        connected=True,
        activity=activity,
        readiness=readiness,
        native_state=str(native),
        filename=filename if isinstance(filename, str) and filename else None,
        progress_percent=progress * 100 if progress is not None else None,
        remaining_seconds=remaining,
        last_seen_at=received_at,
        tools=tools,
        bed=temperature("heater_bed") if "heater_bed" in status else None,
        chamber=temperature(chamber_name) if chamber_name else None,
        error_codes=("klipper_error",) if activity == PrinterActivity.ERROR else (),
    )


class MoonrakerPrinterAdapter(PollingPrinterAdapter):
    family = PrinterFamily.KLIPPER

    @property
    def capabilities(self) -> PrinterCapabilities:
        objects = self._objects or []
        job = "virtual_sdcard" in objects and "print_stats" in objects
        pause = job and "pause_resume" in objects
        return PrinterCapabilities(pause=pause, resume=pause, cancel=pause)

    def __init__(self, api_url: str, *, api_key: str | None = None, transport=None, **kwargs):
        super().__init__(**kwargs)
        self._api_url = api_url.rstrip("/") + "/"
        self._headers = {"X-Api-Key": api_key} if api_key else {}
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._objects: list[str] | None = None
        self._io_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._stopping = True
        self._pending_action: str | None = None
        self._pending_filename: str | None = None
        self._pending_until = 0.0

    async def start(self) -> None:
        self._stopping = False
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._api_url,
                headers=self._headers,
                timeout=self._request_timeout,
                transport=self._transport,
                follow_redirects=False,
                trust_env=False,
            )
        await super().start()

    async def stop(self) -> None:
        self._stopping = True
        await super().stop()
        # Await an in-flight command before its transport can be replaced.
        async with self._command_lock, self._io_lock:
            if self._http is not None:
                await self._http.aclose()
                self._http = None
        self._objects = None
        self._pending_action = None
        self._snapshot = offline_snapshot(self._snapshot)
        self._last_success_at = None

    async def _result(self, method: str, path: str, **kwargs) -> dict:
        if self._http is None:
            raise RuntimeError("Moonraker adapter has not started")
        response = await self._http.request(method, path, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict) or "error" in payload:
            raise ValueError("Invalid Moonraker response")
        return payload["result"]

    async def fetch_status(self) -> FleetPrinterStatus:
        async with self._io_lock:
            return await self._read_status()

    def _observe_command(self, status: FleetPrinterStatus) -> None:
        if self._pending_action is None:
            return
        expected = {"pause": PrinterActivity.PAUSED, "resume": PrinterActivity.PRINTING}
        if (
            time.monotonic() >= self._pending_until
            or status.filename != self._pending_filename
            or status.activity in (PrinterActivity.COMPLETED, PrinterActivity.ERROR, PrinterActivity.IDLE)
            or status.activity == expected.get(self._pending_action)
        ):
            self._pending_action = None

    async def _read_status(self) -> FleetPrinterStatus:
        try:
            info = await self._result("GET", "server/info")
            klippy_state = info.get("klippy_state")
            if klippy_state != "ready":
                if klippy_state not in ("startup", "disconnected", "error", "shutdown"):
                    raise ValueError("Missing Klipper connection state")
                self._objects = None
                failed = klippy_state in ("error", "shutdown")
                return FleetPrinterStatus(
                    connected=True,
                    activity=PrinterActivity.ERROR if failed else PrinterActivity.OFFLINE,
                    readiness=PrinterReadiness.BLOCKED if failed else PrinterReadiness.OFFLINE,
                    native_state=klippy_state,
                    last_seen_at=time.time(),
                    error_codes=("klipper_error",) if failed else (),
                )
            if self._objects is None:
                objects = (await self._result("GET", "printer/objects/list")).get("objects")
                if not isinstance(objects, list) or any(not isinstance(name, str) for name in objects):
                    raise ValueError("Invalid Moonraker object list")
                self._objects = [
                    name
                    for name in objects
                    if name
                    in (
                        "webhooks",
                        "print_stats",
                        "virtual_sdcard",
                        "pause_resume",
                        "heater_bed",
                        "temperature_sensor chamber",
                        "heater_generic chamber",
                    )
                    or re.fullmatch(r"extruder\d*", name)
                ]
            result = await self._result("POST", "printer/objects/query", json={"objects": dict.fromkeys(self._objects)})
            status = result.get("status")
            if not isinstance(status, dict) or any(not isinstance(value, dict) for value in status.values()):
                raise ValueError("Invalid Moonraker status")
            normalized = normalize_status(status, received_at=time.time())
            self._observe_command(normalized)
            return normalized
        except BaseException:
            # Rediscover after reconnect/restart or a failed sample, not on dashboard reads.
            self._objects = None
            raise

    async def _perform_control(self, action: str, filename: str) -> bool:
        async with self._io_lock:
            if self._stopping:
                raise PrinterControlError(503, "Printer connection is stopping")
            # Re-query the actual job on user action; dashboard reads remain cache-only.
            status = await self._read_status()
            self._snapshot = status
            self._last_success_at = time.monotonic()
            allowed = {
                "pause": (PrinterActivity.PRINTING,),
                "resume": (PrinterActivity.PAUSED,),
                "cancel": (PrinterActivity.PRINTING, PrinterActivity.PAUSED),
            }
            if not status.connected or status.readiness != PrinterReadiness.BUSY:
                raise PrinterControlError(409, "Printer is not ready for this print command")
            if status.filename != filename:
                raise PrinterControlError(409, "The active print changed; refresh the card before controlling it")
            if status.activity not in allowed[action]:
                raise PrinterControlError(409, "This command is unavailable in the current printer state")
            if not getattr(self.capabilities, action):
                raise PrinterControlError(409, "This printer configuration does not support this command")
            if self._pending_action is not None:
                raise PrinterControlError(409, "A print command is awaiting confirmation; check the printer state")
            if self._stopping:
                raise PrinterControlError(503, "Printer connection is stopping")
            self._pending_action = action
            self._pending_filename = filename
            self._pending_until = time.monotonic() + 60
            response = await self._http.post(f"printer/print/{action}")
            if response.status_code >= 400:
                self._pending_action = None  # Explicit rejection, unlike a timeout or malformed acknowledgment.
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("result") != "ok" or "error" in payload:
                raise PrinterControlError(
                    502, "Moonraker did not acknowledge the command; check the printer state before retrying"
                )
            return True

    async def _control(self, action: str) -> bool:
        cached = self.get_status()
        filename = cached.filename
        if self._stopping or not cached.connected or not filename:
            raise PrinterControlError(503, "Current printer status is unavailable")
        if self._command_lock.locked():
            raise PrinterControlError(409, "Another print command is in progress")
        async with self._command_lock:
            try:
                return await asyncio.wait_for(self._perform_control(action, filename), timeout=self._request_timeout)
            except PrinterControlError as exc:
                if exc.status_code >= 500:
                    self._snapshot = offline_snapshot(self._snapshot)
                raise
            except (asyncio.TimeoutError, httpx.TimeoutException):
                self._snapshot = offline_snapshot(self._snapshot)
                raise PrinterControlError(
                    504, "Moonraker command timed out; its outcome is unknown. Check printer state before retrying"
                ) from None
            except Exception:
                self._snapshot = offline_snapshot(self._snapshot)
                raise PrinterControlError(
                    502, "Moonraker command failed; check printer state before retrying"
                ) from None

    async def pause(self) -> bool:
        return await self._control("pause")

    async def resume(self) -> bool:
        return await self._control("resume")

    async def cancel(self) -> bool:
        return await self._control("cancel")

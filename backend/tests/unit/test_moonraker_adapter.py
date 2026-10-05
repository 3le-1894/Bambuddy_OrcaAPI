import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.app.models.printer import Printer
from backend.app.services.moonraker_adapter import MoonrakerPrinterAdapter, normalize_status
from backend.app.services.printer_manager import PrinterManager


def sample(state="printing", progress=0.25):
    return {
        "webhooks": {"state": "ready"},
        "print_stats": {"state": state, "filename": "part.gcode", "print_duration": 120},
        "virtual_sdcard": {"progress": progress},
        "extruder": {"temperature": 0, "target": 210},
        "extruder1": {"temperature": 22},
        "heater_bed": {"temperature": 60, "target": 60},
        "temperature_sensor Pi": {"temperature": 50},
    }


@pytest.mark.parametrize(
    "native,activity,readiness",
    [
        ("standby", "idle", "ready"),
        ("printing", "printing", "busy"),
        ("paused", "paused", "busy"),
        ("complete", "completed", "awaiting_clearance"),
        ("cancelled", "idle", "blocked"),
        ("error", "error", "blocked"),
        ("future_state", "unknown", "unknown"),
    ],
)
def test_mapping(native, activity, readiness):
    status = normalize_status(sample(native), received_at=1234)
    assert status.activity == activity
    assert status.readiness == readiness
    assert status.last_seen_at == 1234


def test_measurements_and_estimate():
    status = normalize_status(sample(), received_at=1234)
    assert status.progress_percent == 25
    assert status.remaining_seconds == 360
    assert status.tools[0].current == 0
    assert status.tools[1].target is None
    assert status.chamber is None
    assert status.filename == "part.gcode"
    data = sample()
    data["temperature_sensor chamber"] = {"temperature": 35}
    data["extruder"]["temperature"] = float("nan")
    assert normalize_status(data, received_at=1).chamber.current == 35
    assert normalize_status(data, received_at=1).tools[0].current is None


@pytest.mark.parametrize("progress", [None, 0, True, "0.5", float("inf"), float("nan")])
def test_unavailable_estimate(progress):
    assert normalize_status(sample(progress=progress), received_at=1).remaining_seconds is None


def test_shutdown_overrides_ready_and_missing_objects_never_infer_ready():
    data = sample("standby")
    data["webhooks"] = {"state": "shutdown"}
    status = normalize_status(data, received_at=1)
    assert status.activity == "error" and status.readiness == "blocked"
    assert status.progress_percent is None
    assert normalize_status({}, received_at=1).readiness == "unknown"


@pytest.mark.asyncio
async def test_background_http_auth_prefix_discovery_cache_and_resource_cleanup():
    requests = []

    async def handler(request):
        requests.append(request)
        assert request.headers["X-Api-Key"] == "private-key"
        assert "private-key" not in str(request.url)
        if request.url.path.endswith("server/info"):
            result = {"klippy_state": "ready"}
        elif request.url.path.endswith("objects/list"):
            result = {"objects": list(sample())}
        else:
            assert request.method == "POST"
            assert b"temperature_sensor Pi" not in request.content
            result = {"status": sample()}
        return httpx.Response(200, json={"result": result})

    adapter = MoonrakerPrinterAdapter(
        "http://pi.local:7125/proxy/", api_key="private-key", transport=httpx.MockTransport(handler), poll_interval=0.01
    )
    await adapter.start()
    try:
        for _ in range(100):
            if adapter.get_status().connected:
                break
            await asyncio.sleep(0.001)
        assert adapter.get_status().activity == "printing"
        count = len(requests)
        for _ in range(100):
            adapter.get_status()
        assert len(requests) == count
        await asyncio.sleep(0.03)
        assert sum(r.url.path.endswith("objects/list") for r in requests) == 1
        assert all(r.url.path.startswith("/proxy/") for r in requests)
        assert not adapter.capabilities.pause and not await adapter.pause()
        http = adapter._http
    finally:
        await adapter.stop()
    assert http.is_closed
    assert not adapter.get_status().connected
    await adapter.start()
    await adapter.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["disconnected", "startup", "shutdown", "error"])
async def test_moonraker_alive_but_klipper_not_ready(state):
    def handler(request):
        assert request.url.path == "/server/info"
        return httpx.Response(200, json={"result": {"klippy_state": state}})

    adapter = MoonrakerPrinterAdapter("http://pi", transport=httpx.MockTransport(handler))
    await adapter.start()
    try:
        status = await adapter.fetch_status()
        assert status.connected
        assert status.native_state == state
        assert status.readiness in ("offline", "blocked")
        assert status.tools == ()
    finally:
        await adapter.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,payload",
    [
        (401, {}),
        (403, {}),
        (302, {}),
        (200, {"error": "bad"}),
        (200, {"result": {}}),
        (200, {"result": {"klippy_state": "ready"}}),
    ],
)
async def test_bad_responses_fail_and_do_not_follow_redirects(code, payload):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(code, headers={"Location": "http://elsewhere/"}, json=payload)

    adapter = MoonrakerPrinterAdapter("http://pi", transport=httpx.MockTransport(handler))
    await adapter.start()
    try:
        with pytest.raises((ValueError, httpx.HTTPStatusError)):
            await adapter.fetch_status()
        assert adapter._objects is None
        assert all(request.url.host == "pi" for request in requests)
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_hung_request_deadline_keeps_event_loop_responsive():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    adapter = MoonrakerPrinterAdapter("http://pi", transport=httpx.MockTransport(handler), request_timeout=0.02)
    await adapter.start()
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(cancelled.wait(), 1)
        assert not adapter.get_status().connected
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_manager_wires_monitoring_and_stops_replaced_adapter():
    manager = PrinterManager()
    printer = Printer(
        id=501, name="Klipper", connection_type="klipper", api_url="http://pi:7125", auth_mode="none", is_active=True
    )
    with patch("backend.app.services.printer_manager.MoonrakerPrinterAdapter") as factory:
        first, second = AsyncMock(), AsyncMock()
        factory.side_effect = [first, second]
        assert await manager.connect_printer(printer)
        first.start.assert_awaited_once()
        assert await manager.connect_printer(printer)
        first.stop.assert_awaited_once()
        printer.is_active = False
        assert not await manager.connect_printer(printer)
        second.stop.assert_awaited_once()
        assert manager.get_adapter(501) is None
        assert not manager._clients

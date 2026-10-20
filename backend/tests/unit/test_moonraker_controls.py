import asyncio
import time

import httpx
import pytest

from backend.app.services.moonraker_adapter import MoonrakerPrinterAdapter, normalize_status
from backend.app.services.printer_adapter import PrinterControlError
from backend.tests.unit.test_moonraker_adapter import sample


def make_adapter(world, handler=None, timeout=1):
    requests = []

    async def default_handler(request):
        requests.append(request)
        assert request.headers["X-Api-Key"] == "private-key"
        assert request.url.path.startswith("/prefix/")
        if request.url.path.endswith("server/info"):
            return httpx.Response(200, json={"result": {"klippy_state": "ready"}})
        if request.url.path.endswith("objects/query"):
            data = sample(world.get("state", "printing"))
            data["print_stats"]["filename"] = world.get("filename", "part.gcode")
            return httpx.Response(200, json={"result": {"status": data}})
        assert "/printer/print/" in request.url.path
        assert request.method == "POST"
        if handler:
            return await handler(request)
        world["state"] = {"pause": "paused", "resume": "printing", "cancel": "cancelled"}[
            request.url.path.rsplit("/", 1)[1]
        ]
        return httpx.Response(200, json={"result": "ok"})

    adapter = MoonrakerPrinterAdapter("http://pi:7125/prefix", api_key="private-key", request_timeout=timeout)
    adapter._http = httpx.AsyncClient(
        base_url=adapter._api_url,
        headers=adapter._headers,
        transport=httpx.MockTransport(default_handler),
        follow_redirects=False,
    )
    adapter._objects = ["print_stats", "virtual_sdcard", "pause_resume", "webhooks"]
    adapter._stopping = False
    adapter._snapshot = normalize_status(sample(world.get("state", "printing")), received_at=time.time())
    adapter._last_success_at = time.monotonic()
    return adapter, requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,state", [("pause", "printing"), ("resume", "paused"), ("cancel", "printing"), ("cancel", "paused")]
)
async def test_controls_use_dedicated_authenticated_endpoints_and_no_optimistic_state(action, state):
    adapter, requests = make_adapter({"state": state})
    try:
        assert getattr(adapter.capabilities, action)
        assert await getattr(adapter, action)()
        commands = [request for request in requests if "/printer/print/" in request.url.path]
        assert len(commands) == 1
        assert commands[0].url.path == f"/prefix/printer/print/{action}"
        assert adapter.get_status().native_state == state  # Actual state changes only on telemetry.
        assert all("private-key" not in str(request.url) for request in requests)
    finally:
        await adapter.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,state",
    [("pause", "paused"), ("resume", "printing"), ("cancel", "standby"), ("pause", "error"), ("resume", "complete")],
)
async def test_fresh_state_prevents_wrong_commands(action, state):
    adapter, requests = make_adapter({"state": state})
    try:
        with pytest.raises(PrinterControlError) as error:
            await getattr(adapter, action)()
        assert error.value.status_code == 409
        assert not any("/printer/print/" in request.url.path for request in requests)
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_changed_job_and_missing_pause_module_prevent_dispatch():
    world = {"filename": "different.gcode"}
    adapter, requests = make_adapter(world)
    try:
        with pytest.raises(PrinterControlError, match="active print changed"):
            await adapter.cancel()
        world["filename"] = "part.gcode"
        adapter._snapshot = normalize_status(sample(), received_at=time.time())
        adapter._objects.remove("pause_resume")
        assert not adapter.capabilities.cancel
        with pytest.raises(PrinterControlError, match="does not support"):
            await adapter.cancel()
        assert not any("/printer/print/" in request.url.path for request in requests)
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_pending_commands_are_not_repeated_until_observed_state_changes():
    async def unchanged(request):
        return httpx.Response(200, json={"result": "ok"})

    world = {"state": "printing"}
    adapter, requests = make_adapter(world, unchanged)
    try:
        results = await asyncio.gather(adapter.pause(), adapter.pause(), return_exceptions=True)
        assert results[0] is True
        assert isinstance(results[1], PrinterControlError)
        with pytest.raises(PrinterControlError, match="awaiting confirmation"):
            await adapter.pause()
        assert sum("/printer/print/" in request.url.path for request in requests) == 1
        world["state"] = "paused"
        assert await adapter.resume()
        assert sum("/printer/print/" in request.url.path for request in requests) == 2
    finally:
        await adapter.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,payload",
    [
        (401, {"error": "private-key"}),
        (500, {"error": "private-key"}),
        (302, {}),
        (200, {"result": "unexpected"}),
        (200, {"error": "private-key"}),
    ],
)
async def test_rejected_or_malformed_acknowledgment_is_safe_and_never_retried(code, payload):
    async def reject(request):
        return httpx.Response(code, headers={"Location": "http://other-host"}, json=payload)

    adapter, requests = make_adapter({}, reject)
    try:
        with pytest.raises(PrinterControlError) as error:
            await adapter.pause()
        assert error.value.status_code == 502
        assert "private-key" not in str(error.value)
        assert sum("/printer/print/" in request.url.path for request in requests) == 1
        assert not adapter.get_status().connected
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_timeout_keeps_event_loop_live_and_reports_unknown_outcome():
    cancelled = asyncio.Event()

    async def hang(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    adapter, requests = make_adapter({}, hang, timeout=0.02)
    try:
        with pytest.raises(PrinterControlError) as error:
            await adapter.cancel()
        assert error.value.status_code == 504
        assert "outcome is unknown" in str(error.value)
        assert cancelled.is_set()
        assert adapter._pending_action == "cancel"
        assert sum("/printer/print/" in request.url.path for request in requests) == 1
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_stop_waits_for_inflight_command_and_stopped_adapter_cannot_send():
    started, release = asyncio.Event(), asyncio.Event()

    async def command(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"result": "ok"})

    adapter, requests = make_adapter({}, command)
    http = adapter._http
    control = asyncio.create_task(adapter.pause())
    await started.wait()
    stop = asyncio.create_task(adapter.stop())
    await asyncio.sleep(0)
    assert not stop.done() and not http.is_closed
    release.set()
    assert await control
    await stop
    assert http.is_closed and not adapter.get_status().connected
    with pytest.raises(PrinterControlError):
        await adapter.resume()
    assert sum("/printer/print/" in request.url.path for request in requests) == 1


@pytest.mark.asyncio
async def test_stale_cached_snapshot_cannot_send():
    adapter, requests = make_adapter({})
    adapter._last_success_at = time.monotonic() - 100
    try:
        with pytest.raises(PrinterControlError) as error:
            await adapter.pause()
        assert error.value.status_code == 503
        assert not requests
    finally:
        await adapter.stop()

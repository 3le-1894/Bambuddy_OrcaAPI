from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.models.printer import Printer
from backend.app.services.printer_adapter import (
    FleetPrinterStatus,
    PrinterActivity,
    PrinterCapabilities,
    PrinterControlError,
    PrinterReadiness,
)


@pytest.fixture
async def fleet_control_setup(db_session, monkeypatch):
    printer = Printer(
        name="Athena", connection_type="klipper", api_url="http://pi:7125", auth_mode="none", is_active=True
    )
    db_session.add(printer)
    await db_session.commit()
    await db_session.refresh(printer)
    status = FleetPrinterStatus(
        connected=True,
        activity=PrinterActivity.PRINTING,
        readiness=PrinterReadiness.BUSY,
        native_state="printing",
        filename="part.gcode",
    )
    adapter = MagicMock()
    adapter.capabilities = PrinterCapabilities(pause=True, resume=True, cancel=True)
    adapter.pause = AsyncMock(return_value=True)
    adapter.resume = AsyncMock(return_value=True)
    adapter.cancel = AsyncMock(return_value=True)
    adapter.get_status.return_value = status
    monkeypatch.setattr("backend.app.api.routes.printers.printer_manager.get_adapter", lambda _id: adapter)
    monkeypatch.setattr(
        "backend.app.api.routes.printers.printer_manager.get_fleet_status", lambda _id: adapter.get_status()
    )
    return printer, adapter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,state",
    [
        ("pause", PrinterActivity.PRINTING),
        ("resume", PrinterActivity.PAUSED),
        ("cancel", PrinterActivity.PRINTING),
        ("cancel", PrinterActivity.PAUSED),
    ],
)
async def test_controls_submit_through_adapter(async_client, fleet_control_setup, action, state):
    printer, adapter = fleet_control_setup
    adapter.get_status.return_value = replace(adapter.get_status(), activity=state)
    result = await async_client.post(
        f"/api/v1/printers/{printer.id}/fleet-control/{action}",
        json={"expected_filename": "part.gcode", "confirmed": action == "cancel"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["success"]
    getattr(adapter, action).assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,code",
    [
        ("offline", 503),
        ("idle", 409),
        ("error", 409),
        ("disabled", 409),
        ("unsupported", 409),
        ("different_file", 409),
        ("unconfirmed", 409),
        ("invalid_action", 422),
        ("missing_filename", 422),
        ("missing_printer", 404),
        ("duet", 409),
        ("bambu", 409),
    ],
)
async def test_guards_prevent_command_dispatch(async_client, db_session, fleet_control_setup, case, code):
    printer, adapter = fleet_control_setup
    action = "cancel"
    body = {"expected_filename": "part.gcode", "confirmed": True}
    target_id = printer.id
    if case == "offline":
        adapter.get_status.return_value = replace(adapter.get_status(), connected=False)
    elif case in ("idle", "error"):
        adapter.get_status.return_value = replace(
            adapter.get_status(), activity=PrinterActivity(case), readiness=PrinterReadiness.BLOCKED
        )
    elif case == "disabled":
        printer.is_active = False
    elif case == "unsupported":
        adapter.capabilities = PrinterCapabilities()
    elif case == "different_file":
        body["expected_filename"] = "old.gcode"
    elif case == "unconfirmed":
        body["confirmed"] = False
    elif case == "invalid_action":
        action = "restart"
    elif case == "missing_filename":
        body.pop("expected_filename")
    elif case == "missing_printer":
        target_id = printer.id + 999
    elif case in ("duet", "bambu"):
        printer.connection_type = case
    await db_session.commit()
    result = await async_client.post(f"/api/v1/printers/{target_id}/fleet-control/{action}", json=body)
    assert result.status_code == code, result.text
    for method in (adapter.pause, adapter.resume, adapter.cancel):
        method.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,code",
    [
        (PrinterControlError(504, "Outcome unknown; check printer state"), 504),
        (PrinterControlError(409, "Print changed"), 409),
        (RuntimeError("private-secret"), 502),
        (False, 502),
    ],
)
async def test_errors_are_reported_without_secrets_or_retries(async_client, fleet_control_setup, failure, code):
    printer, adapter = fleet_control_setup
    if isinstance(failure, Exception):
        adapter.pause.side_effect = failure
    else:
        adapter.pause.return_value = failure
    result = await async_client.post(
        f"/api/v1/printers/{printer.id}/fleet-control/pause", json={"expected_filename": "part.gcode"}
    )
    assert result.status_code == code
    assert "private-secret" not in result.text
    adapter.pause.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["header", "bearer"])
async def test_control_permissions_and_api_key_printer_scope(async_client, db_session, fleet_control_setup, transport):
    from backend.app.core.auth import generate_api_key
    from backend.app.models.api_key import APIKey

    printer, adapter = fleet_control_setup
    setup = await async_client.post(
        "/api/v1/auth/setup",
        json={"auth_enabled": True, "admin_username": "fleetadmin", "admin_password": "FleetAdminPass1!"},
    )
    assert setup.status_code == 200, setup.text
    login = await async_client.post(
        "/api/v1/auth/login", json={"username": "fleetadmin", "password": "FleetAdminPass1!"}
    )
    admin = {"Authorization": f"Bearer {login.json()['access_token']}"}
    groups = (await async_client.get("/api/v1/groups/", headers=admin)).json()
    viewers = next(group for group in groups if group["name"] == "Viewers")
    created = await async_client.post(
        "/api/v1/users/",
        headers=admin,
        json={"username": "fleetviewer", "password": "FleetViewerPass1!", "group_ids": [viewers["id"]]},
    )
    assert created.status_code == 201, created.text
    login = await async_client.post(
        "/api/v1/auth/login", json={"username": "fleetviewer", "password": "FleetViewerPass1!"}
    )
    viewer = {"Authorization": f"Bearer {login.json()['access_token']}"}
    url = f"/api/v1/printers/{printer.id}/fleet-control/pause"
    body = {"expected_filename": "part.gcode"}
    assert (await async_client.post(url, headers=viewer, json=body)).status_code == 403
    assert (await async_client.post(url, json=body)).status_code == 401
    for allowed, ids in ((False, [printer.id]), (True, [printer.id + 99]), (True, [printer.id])):
        full, hashed, prefix = generate_api_key()
        db_session.add(
            APIKey(
                name="fleet-key",
                key_hash=hashed,
                key_prefix=prefix,
                can_control_printer=allowed,
                printer_ids=ids,
                enabled=True,
            )
        )
        await db_session.commit()
        headers = {"X-API-Key": full} if transport == "header" else {"Authorization": f"Bearer {full}"}
        result = await async_client.post(url, headers=headers, json=body)
        assert result.status_code == (200 if allowed and printer.id in ids else 403), result.text
    adapter.pause.assert_awaited_once()

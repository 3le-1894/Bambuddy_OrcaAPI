from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.app.models.printer import Printer


@pytest.mark.asyncio
async def test_configuration_only_create_edit_redact_and_block_transport(async_client, db_session):
    with (
        patch("backend.app.services.printer_manager.printer_manager.test_connection", new=AsyncMock()) as probe,
        patch("backend.app.services.printer_manager.printer_manager.connect_printer", new=AsyncMock()) as connect,
    ):
        response = await async_client.post(
            "/api/v1/printers/",
            json={
                "name": "Test Klipper",
                "connection_type": "klipper",
                "api_url": "http://pi.local:7125",
                "auth_mode": "api_key",
                "connection_secret": "test-credential",
            },
        )
        assert response.status_code == 200, response.text
        row = response.json()
        printer_id = row["id"]
        assert row["is_active"] is False
        assert row["connection_supported"] is False
        assert row["has_connection_secret"] is True
        probe.assert_not_awaited()
        connect.assert_not_awaited()

        for endpoint in (f"/api/v1/printers/{printer_id}", "/api/v1/printers/"):
            read = await async_client.get(endpoint)
            assert read.status_code == 200
            assert "test-credential" not in read.text
            assert '"connection_secret"' not in read.text

        updated = await async_client.patch(f"/api/v1/printers/{printer_id}", json={"api_url": "http://pi.local:7126"})
        assert updated.status_code == 200, updated.text
        assert updated.json()["has_connection_secret"]
        saved = (await db_session.execute(select(Printer).where(Printer.id == printer_id))).scalar_one()
        await db_session.refresh(saved)
        assert saved.connection_secret == "test-credential"

        for endpoint in ("connect", "print/pause"):
            blocked = await async_client.post(f"/api/v1/printers/{printer_id}/{endpoint}")
            assert blocked.status_code == 409
        assert (await async_client.patch(f"/api/v1/printers/{printer_id}", json={"is_active": True})).status_code == 409
        assert (
            await async_client.patch(f"/api/v1/printers/{printer_id}", json={"connection_type": "duet"})
        ).status_code == 409
        removed = await async_client.patch(f"/api/v1/printers/{printer_id}", json={"auth_mode": "none"})
        assert removed.status_code == 200, removed.text
        assert not removed.json()["has_connection_secret"]

        invalid = await async_client.patch(
            f"/api/v1/printers/{printer_id}",
            json={
                "auth_mode": "password",
                "connection_secret": "must-not-appear-in-error",
            },
        )
        assert invalid.status_code == 422
        assert "must-not-appear-in-error" not in invalid.text

        duet = await async_client.post(
            "/api/v1/printers/",
            json={
                "name": "Duet SBC",
                "connection_type": "duet",
                "api_url": "http://duet.local",
                "duet_mode": "sbc",
                "auth_mode": "password",
                "connection_secret": "duet-private-password",
            },
        )
        assert duet.status_code == 200, duet.text
        assert duet.json()["duet_mode"] == "sbc"
        assert "duet-private-password" not in duet.text


@pytest.mark.asyncio
async def test_same_host_separate_endpoints_create_distinct_printers(async_client):
    ids = []
    for port in (7125, 7126):
        response = await async_client.post(
            "/api/v1/printers/",
            json={
                "name": f"Klipper {port}",
                "connection_type": "klipper",
                "api_url": f"http://pi.local:{port}",
            },
        )
        assert response.status_code == 200, response.text
        ids.append(response.json()["id"])
    assert ids[0] != ids[1]

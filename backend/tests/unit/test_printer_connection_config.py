from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.printer_connection_migration import migrate_printer_connections
from backend.app.models.printer import Printer
from backend.app.schemas.printer import PrinterCreate, PrinterResponse


def config(**values):
    return PrinterCreate(name="Printer", connection_type="klipper", api_url="http://pi.local:7125", **values)


def test_non_bambu_has_no_fabricated_identity():
    first = config()
    second = PrinterCreate(name="Second", connection_type="klipper", api_url="http://pi.local:7126")
    assert first.serial_number is second.serial_number is None
    assert first.ip_address is first.access_code is None
    assert first.api_url != second.api_url


@pytest.mark.parametrize(
    "url",
    [
        "ftp://pi.local",
        "http://user:secret@pi.local",
        "http://pi.local?key=secret",
        "http://pi.local/#secret",
        "http://pi.local:99999",
        "http://pi.local:0",
        "http://bad host",
    ],
)
def test_invalid_server_urls_rejected(url):
    with pytest.raises(ValidationError):
        PrinterCreate(name="Printer", connection_type="klipper", api_url=url)


def test_url_preserves_proxy_prefix_and_ipv6():
    data = PrinterCreate(name="Printer", connection_type="klipper", api_url="https://[::1]:7125/moonraker/")
    assert data.api_url == "https://[::1]:7125/moonraker"


@pytest.mark.parametrize(
    "values",
    [
        {"auth_mode": "api_key"},
        {"auth_mode": "password", "connection_secret": "secret"},
        {"connection_secret": "secret"},
        {"duet_mode": "sbc"},
        {"serial_number": "fake"},
    ],
)
def test_klipper_rejects_invalid_auth_and_legacy_fields(values):
    with pytest.raises(ValidationError):
        config(**values)


def test_secret_is_input_only_and_not_in_repr():
    data = config(auth_mode="api_key", connection_secret="test-private-value")
    assert "connection_secret" not in data.model_dump()
    assert "test-private-value" not in repr(data)


def test_duet_modes_and_authentication():
    standalone = PrinterCreate(name="Duet", connection_type="duet", api_url="http://duet.local")
    assert standalone.duet_mode == "standalone"
    sbc = PrinterCreate(
        name="Duet",
        connection_type="duet",
        api_url="http://duet.local",
        duet_mode="sbc",
        auth_mode="password",
        connection_secret="test-value",
    )
    assert sbc.duet_mode == "sbc"


def test_non_bambu_response_has_no_secret_or_internal_credentials():
    row = Printer(
        id=2,
        name="Klipper",
        connection_type="klipper",
        auth_mode="api_key",
        api_url="http://pi.local",
        serial_number=None,
        ip_address=None,
        access_code=None,
        model=None,
        location=None,
        auto_archive=True,
        external_camera_enabled=False,
        camera_rotation=0,
        is_active=False,
        nozzle_count=1,
        print_hours_offset=0,
        plate_detection_enabled=False,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    row.connection_secret = "test-private-value"
    response = PrinterResponse.from_orm_with_roi(row).model_dump()
    assert response["serial_number"] == ""
    assert response["has_connection_secret"]
    assert response["connection_supported"]
    assert "connection_secret" not in response and "access_code" not in response
    assert "test-private-value" not in str(response)


@pytest.mark.asyncio
async def test_migration_preserves_records_relations_indexes_and_is_idempotent(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
            await conn.execute(
                text(
                    "CREATE TABLE printers (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, "
                    "serial_number VARCHAR(50) NOT NULL UNIQUE, ip_address VARCHAR(253) NOT NULL, "
                    "access_code VARCHAR(20) NOT NULL)"
                )
            )
            await conn.execute(
                text("CREATE TABLE history (id INTEGER PRIMARY KEY, printer_id INTEGER REFERENCES printers(id))")
            )
            await conn.execute(text("CREATE INDEX history_printer_idx ON history(printer_id)"))
            await conn.execute(text("INSERT INTO printers VALUES (7, 'Existing', 'SERIAL', '192.168.1.2', 'code')"))
            await conn.execute(text("INSERT INTO history VALUES (8, 7)"))
            await migrate_printer_connections(conn)
            await migrate_printer_connections(conn)
            old = (
                await conn.execute(
                    text("SELECT id, serial_number, access_code, connection_type FROM printers WHERE id=7")
                )
            ).one()
            assert tuple(old) == (7, "SERIAL", "code", "bambu")
            assert (await conn.execute(text("SELECT printer_id FROM history WHERE id=8"))).scalar_one() == 7
            for port in (7125, 7126):
                await conn.execute(
                    text("INSERT INTO printers(name, connection_type, api_url) VALUES ('New', 'klipper', :url)"),
                    {"url": f"http://pi.local:{port}"},
                )
            assert (await conn.execute(text("PRAGMA integrity_check"))).scalar_one() == "ok"
            assert not (await conn.execute(text("PRAGMA foreign_key_check"))).all()
            assert (
                await conn.execute(text("SELECT name FROM sqlite_master WHERE name='history_printer_idx'"))
            ).scalar_one()
        # Reopen after commit to verify schema reload, not only this connection's cache.
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT COUNT(*) FROM printers"))).scalar_one() == 3
            assert not any(
                row[3]
                for row in (await conn.execute(text("PRAGMA table_info(printers)"))).all()
                if row[1] in {"serial_number", "ip_address", "access_code"}
            )
    finally:
        await engine.dispose()


def test_credentials_are_encrypted_and_fail_closed_without_a_key():
    printer = Printer()
    printer.connection_secret = "private-api-key"
    assert printer._connection_secret_enc.startswith("fernet:")
    assert printer.connection_secret == "private-api-key"
    with patch("backend.app.models.printer.mfa_encrypt", return_value="plain-secret"), pytest.raises(RuntimeError):
        printer.connection_secret = "plain-secret"
    assert printer.connection_secret == "private-api-key"


@pytest.mark.asyncio
async def test_postgres_migration_uses_additive_columns_and_nullable_ddl():
    conn = MagicMock()
    conn.dialect.name = "postgresql"
    conn.execute = AsyncMock()
    with patch("backend.app.core.database._safe_execute", new=AsyncMock()) as ddl:
        await migrate_printer_connections(conn)
    assert ddl.await_count == 5
    assert {str(call.args[0]) for call in conn.execute.await_args_list} == {
        f"ALTER TABLE printers ALTER COLUMN {column} DROP NOT NULL"
        for column in ("serial_number", "ip_address", "access_code")
    }

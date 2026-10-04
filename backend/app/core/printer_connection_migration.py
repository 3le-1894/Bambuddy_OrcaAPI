"""Preserve printer rows/relationships while adding mixed-fleet configuration."""

import re
import sqlite3

from sqlalchemy import text


async def migrate_printer_connections(conn):
    from backend.app.core.database import _safe_execute

    for column in (
        "connection_type VARCHAR(20) NOT NULL DEFAULT 'bambu'",
        "api_url VARCHAR(500)",
        "auth_mode VARCHAR(20) NOT NULL DEFAULT 'none'",
        "duet_mode VARCHAR(20)",
        "connection_secret TEXT",
    ):
        await _safe_execute(conn, f"ALTER TABLE printers ADD COLUMN {column}")

    if conn.dialect.name != "sqlite":
        for column in ("serial_number", "ip_address", "access_code"):
            await conn.execute(text(f"ALTER TABLE printers ALTER COLUMN {column} DROP NOT NULL"))
        return

    columns = (await conn.execute(text("PRAGMA table_info(printers)"))).all()
    required = {row[1] for row in columns if row[3]} & {"serial_number", "ip_address", "access_code"}
    if not required:
        return
    original = (
        await conn.execute(text("SELECT sql FROM sqlite_master WHERE name='printers' AND type='table'"))
    ).scalar_one()
    updated = original
    for column in required:
        pattern = rf'(?<!\w)("?{column}"?\s+VARCHAR\s*\(\s*\d+\s*\))\s+NOT\s+NULL\b'
        updated, count = re.subn(pattern, r"\1", updated, flags=re.IGNORECASE)
        if count != 1:
            raise RuntimeError(f"Cannot safely relax printer column {column}")
    # Parse the candidate schema first. No live table rebuild, row copying or
    # DROP TABLE: foreign keys, indexes, triggers and IDs stay untouched.
    with sqlite3.connect(":memory:") as validation:
        validation.execute(updated)
    version = (await conn.execute(text("PRAGMA schema_version"))).scalar_one()
    try:
        await conn.execute(text("PRAGMA writable_schema = ON"))
        await conn.execute(
            text("UPDATE sqlite_master SET sql=:schema WHERE name='printers' AND type='table'"), {"schema": updated}
        )
        await conn.execute(text(f"PRAGMA schema_version = {version + 1}"))
    finally:
        await conn.execute(text("PRAGMA writable_schema = OFF"))
    remaining = (await conn.execute(text("PRAGMA table_info(printers)"))).all()
    if any(row[3] for row in remaining if row[1] in required):
        raise RuntimeError("Printer schema cache did not refresh")

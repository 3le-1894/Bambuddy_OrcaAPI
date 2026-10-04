"""Run with python -m unittest discover -s tests -p test_desktop_profile_sync.py."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="bambuddy-sync-test-"))

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.database import Base
from backend.app.models.local_preset import LocalPreset
from backend.app.models.settings import Settings
from backend.app.services import desktop_profile_sync as sync


class DesktopSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(
                lambda c: Base.metadata.create_all(c, tables=[Settings.__table__, LocalPreset.__table__])
            )
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.db.add(Settings(key="orcaslicer_api_url", value="http://sidecar"))
        await self.db.flush()
        self.profiles = [
            {
                "key": "filament/PLA.json",
                "name": "PLA",
                "preset_type": "filament",
                "setting": {"name": "PLA", "filament_type": ["PLA"]},
            }
        ]
        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"profiles": self.profiles}))
        self.client_patch = patch.object(
            sync.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs)
        )
        self.client_patch.start()
        self.token_patch = patch.dict(os.environ, {"DESKTOP_PROFILE_SYNC_TOKEN": "test-token"})
        self.token_patch.start()
        self.directory = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(sync.settings, "base_dir", Path(self.directory.name))
        self.path_patch.start()

    async def asyncTearDown(self):
        self.path_patch.stop()
        self.client_patch.stop()
        self.token_patch.stop()
        await self.db.close()
        await self.engine.dispose()
        self.directory.cleanup()

    async def test_add_update_unchanged_and_backup(self):
        self.assertEqual((await sync.sync_desktop_profiles(self.db))["added"], 1)
        self.assertEqual((await sync.sync_desktop_profiles(self.db))["unchanged"], 1)
        self.profiles[0]["setting"]["filament_cost"] = "20"
        self.assertEqual((await sync.sync_desktop_profiles(self.db))["updated"], 1)
        self.assertTrue(list(Path(self.directory.name).rglob("*.json")))

    async def test_preserves_bambuddy_edits(self):
        await sync.sync_desktop_profiles(self.db)
        preset = (await self.db.execute(select(LocalPreset))).scalar_one()
        preset.setting = json.dumps({"name": "PLA", "filament_cost": "99"})
        result = await sync.sync_desktop_profiles(self.db)
        self.assertEqual(result["conflicts"], ["PLA"])
        self.assertEqual(json.loads(preset.setting)["filament_cost"], "99")

    async def test_preserves_unrelated_same_name(self):
        self.db.add(LocalPreset(name="PLA", preset_type="filament", source="manual", setting="{}"))
        await self.db.flush()
        result = await sync.sync_desktop_profiles(self.db)
        self.assertEqual(result["conflicts"], ["PLA"])
        self.assertEqual(result["added"], 0)

    async def test_adopts_previously_synced_id(self):
        preset = LocalPreset(name="PLA", preset_type="filament", source="manual", setting="{}")
        self.db.add(preset)
        await self.db.flush()
        self.profiles[0]["legacy_id"] = preset.id
        self.assertEqual((await sync.sync_desktop_profiles(self.db))["updated"], 1)
        self.assertEqual(preset.source, "desktop_sync")

    async def test_reports_removed_profiles_without_deleting(self):
        await sync.sync_desktop_profiles(self.db)
        self.profiles = [
            {"key": "filament/PETG.json", "name": "PETG", "preset_type": "filament", "setting": {"name": "PETG"}}
        ]
        self.assertEqual((await sync.sync_desktop_profiles(self.db))["missing"], ["PLA"])
        self.assertEqual(len((await self.db.execute(select(LocalPreset))).scalars().all()), 2)

    async def test_rejects_duplicates_before_database_changes(self):
        self.profiles.append(dict(self.profiles[0]))
        with self.assertRaises(HTTPException):
            await sync.sync_desktop_profiles(self.db)
        self.assertEqual(len((await self.db.execute(select(LocalPreset))).scalars().all()), 0)

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
from backend.app.models.slot_preset import SlotPresetMapping  # noqa: F401
from backend.app.services import desktop_profile_sync as sync


class DesktopSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(lambda c: Base.metadata.create_all(c))
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
        transport = httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json=(
                    {"status": "healthy", "checks": {"orcaslicer": {"available": True, "version": "2.5.0-dev"}}}
                    if req.url.path == "/health"
                    else {"profiles": self.profiles}
                ),
            )
        )
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

    async def test_status_preserves_result_names_and_reports_connection(self):
        await sync.sync_desktop_profiles(self.db)
        status = await sync.sync_status(self.db)
        self.assertEqual(status["profile_count"], 1)
        self.assertEqual(status["sidecar"], {"status": "connected", "version": "2.5.0-dev"})
        self.assertEqual(status["changes"], [{"name": "PLA", "preset_type": "filament", "action": "added"}])

    async def test_status_when_sync_is_not_configured(self):
        with patch.dict(os.environ, {"DESKTOP_PROFILE_SYNC_TOKEN": ""}):
            status = await sync.sync_status(self.db)
        self.assertEqual(status["sidecar"]["status"], "not_configured")
        self.assertIsNone(status["synced_at"])

    async def missing_profile(self):
        await sync.sync_desktop_profiles(self.db)
        preset = (await self.db.execute(select(LocalPreset))).scalar_one()
        self.profiles = [
            {"key": "filament/PETG.json", "name": "PETG", "preset_type": "filament", "setting": {"name": "PETG"}}
        ]
        return preset

    async def test_cleanup_previews_then_removes_with_backup(self):
        preset = await self.missing_profile()
        preview = await sync.cleanup_desktop_profiles(self.db)
        self.assertEqual(preview["profiles"][0]["reasons"], [])
        self.assertIsNotNone(await self.db.get(LocalPreset, preset.id))
        result = await sync.cleanup_desktop_profiles(self.db, [preset.id])
        self.assertEqual(result["removed"], ["PLA"])
        self.assertIsNone(await self.db.get(LocalPreset, preset.id))
        backups = [json.loads(p.read_text()) for p in Path(self.directory.name).rglob("*.json")]
        self.assertTrue(any(any(p["name"] == "PLA" for p in b["presets"]) for b in backups))
        self.assertEqual((await sync.sync_status(self.db))["profile_count"], 0)

    async def test_cleanup_rechecks_edits_and_reappearing_profiles(self):
        preset = await self.missing_profile()
        await sync.cleanup_desktop_profiles(self.db)
        preset.setting = '{"name":"PLA","filament_cost":"99"}'
        self.assertIn(
            "Edited in farm manager", (await sync.cleanup_desktop_profiles(self.db))["profiles"][0]["reasons"]
        )
        with self.assertRaises(HTTPException) as raised:
            await sync.cleanup_desktop_profiles(self.db, [preset.id])
        self.assertEqual(raised.exception.status_code, 409)
        self.profiles.append(
            {"key": "filament/PLA.json", "name": "PLA", "preset_type": "filament", "setting": {"name": "PLA"}}
        )
        self.assertEqual((await sync.cleanup_desktop_profiles(self.db))["profiles"], [])

    async def test_cleanup_protects_spool_and_profile_references(self):
        from backend.app.models.spool import Spool

        preset = await self.missing_profile()
        self.db.add(Spool(material="PLA", label_weight=1000, core_weight=0, slicer_filament=str(preset.id)))
        self.db.add(LocalPreset(name="Child", preset_type="filament", source="manual", setting='{"inherits":"PLA"}'))
        await self.db.flush()
        reasons = (await sync.cleanup_desktop_profiles(self.db))["profiles"][0]["reasons"]
        self.assertIn("Assigned to an inventory spool", reasons)
        self.assertIn("Referenced by another profile", reasons)

    async def test_cleanup_rejects_empty_scan_and_unknown_ids(self):
        preset = await self.missing_profile()
        with self.assertRaises(HTTPException):
            await sync.cleanup_desktop_profiles(self.db, [999999])
        self.profiles = []
        with self.assertRaises(HTTPException):
            await sync.cleanup_desktop_profiles(self.db, [preset.id])
        self.assertIsNotNone(await self.db.get(LocalPreset, preset.id))

    async def test_manual_deletion_clears_stale_missing_warning(self):
        preset = await self.missing_profile()
        self.assertEqual((await sync.sync_desktop_profiles(self.db))["missing"], ["PLA"])
        await self.db.delete(preset)
        await self.db.flush()
        self.assertEqual((await sync.sync_status(self.db))["missing"], [])
        self.assertEqual((await sync.cleanup_desktop_profiles(self.db))["profiles"], [])
        self.assertEqual((await sync.sync_desktop_profiles(self.db))["missing"], [])
        state = json.loads((await sync.setting_row(self.db, sync.STATE_KEY)).value)
        self.assertNotIn("filament/PLA.json", state["managed"])

    async def test_merged_identity_migrates_nested_key_preserving_id(self):
        self.profiles[0]["key"] = "filament/base/PLA.json"
        await sync.sync_desktop_profiles(self.db)
        preset = (await self.db.execute(select(LocalPreset))).scalar_one()
        original_id = preset.id
        self.profiles[0]["key"] = "filament/PLA.json"
        self.profiles[0]["setting"]["filament_cost"] = "25"
        result = await sync.sync_desktop_profiles(self.db)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["missing"], [])
        self.assertEqual((await self.db.execute(select(LocalPreset))).scalar_one().id, original_id)
        state = json.loads((await sync.setting_row(self.db, sync.STATE_KEY)).value)
        self.assertEqual(list(state["managed"]), ["filament/PLA.json"])

    async def test_identity_migration_preserves_real_edits(self):
        self.profiles[0]["key"] = "filament/base/PLA.json"
        await sync.sync_desktop_profiles(self.db)
        preset = (await self.db.execute(select(LocalPreset))).scalar_one()
        preset.setting = '{"name":"PLA","filament_cost":"99"}'
        self.profiles[0]["key"] = "filament/PLA.json"
        result = await sync.sync_desktop_profiles(self.db)
        self.assertEqual(result["conflicts"], ["PLA"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(json.loads(preset.setting)["filament_cost"], "99")

    async def test_cleanup_ignores_deleted_pipeline_references(self):
        from backend.app.models.slicer_pipeline import SlicerPipeline

        preset = await self.missing_profile()
        pipeline = SlicerPipeline(
            name="Deleted pipeline",
            printer_preset_source="standard",
            printer_preset_id="printer",
            process_preset_source="local",
            process_preset_id=str(preset.id),
            filament_presets_json="[]",
            is_deleted=True,
        )
        self.db.add(pipeline)
        await self.db.flush()
        preview = await sync.cleanup_desktop_profiles(self.db)
        self.assertEqual(preview["profiles"][0]["reasons"], [])
        await sync.cleanup_desktop_profiles(self.db, [preset.id])
        self.assertIsNone(await self.db.get(LocalPreset, preset.id))
        self.assertIsNotNone(await self.db.get(SlicerPipeline, pipeline.id))

    async def test_cleanup_protects_slot_and_pipeline_references(self):
        from backend.app.models.slicer_pipeline import SlicerPipeline

        preset = await self.missing_profile()
        self.db.add(
            SlotPresetMapping(
                printer_id=1, ams_id=0, tray_id=0, preset_id=str(preset.id), preset_name="PLA", preset_source="local"
            )
        )
        self.db.add(
            SlicerPipeline(
                name="Test",
                printer_preset_source="standard",
                printer_preset_id="printer",
                process_preset_source="standard",
                process_preset_id="process",
                filament_presets_json=json.dumps([{"source": "local", "id": str(preset.id)}]),
            )
        )
        await self.db.flush()
        reasons = (await sync.cleanup_desktop_profiles(self.db))["profiles"][0]["reasons"]
        self.assertIn("Assigned to a printer slot", reasons)
        self.assertIn("Used by a slicing pipeline", reasons)
        with self.assertRaises(HTTPException):
            await sync.cleanup_desktop_profiles(self.db, [preset.id])

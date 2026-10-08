"""Import saved desktop Orca profiles from the configured companion API."""

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.models.local_preset import LocalPreset
from backend.app.models.settings import Settings
from backend.app.services.orca_profiles import extract_core_fields

STATE_KEY = "desktop_profile_sync_state"
sync_lock = asyncio.Lock()


class DesktopCleanupRequest(BaseModel):
    # Omission previews; explicit IDs apply only the reviewed selection.
    ids: list[int] | None = Field(default=None, min_length=1, max_length=5000)


async def cleanup_desktop_profiles(db: AsyncSession, ids: list[int] | None = None) -> dict:
    """Re-scan the desktop before previewing or removing managed copies."""
    from backend.app.models.slicer_pipeline import SlicerPipeline
    from backend.app.models.slot_preset import SlotPresetMapping
    from backend.app.models.spool import Spool
    from backend.app.models.spool_filament_preset import SpoolFilamentPreset, SpoolmanFilamentPreset

    if sync_lock.locked():
        raise HTTPException(409, "A desktop profile sync is already running.")
    async with sync_lock:
        incoming = await _desktop_profiles(db)
        keys = {p.key for p in incoming}
        row = await setting_row(db, STATE_KEY)
        state = json.loads(row.value) if row else {"managed": {}}
        managed = state.get("managed", {})
        existing = list((await db.execute(select(LocalPreset))).scalars())
        by_id = {p.id: p for p in existing}
        # Read all reference owners, including archived spools. A saved pipeline
        # or a spool override can still need the profile after it leaves the desktop.
        spool_refs = list((await db.execute(select(Spool.slicer_filament, Spool.slicer_filament_name))).all())
        for model in (SpoolFilamentPreset, SpoolmanFilamentPreset):
            spool_refs.extend((await db.execute(select(model.slicer_filament, model.slicer_filament_name))).all())
        pipelines = list(
            (await db.execute(select(SlicerPipeline).where(SlicerPipeline.is_deleted.is_(False)))).scalars()
        )
        slots = list((await db.execute(select(SlotPresetMapping))).scalars())
        preview = []
        for key, record in managed.items():
            preset = by_id.get(record["id"])
            if key in keys or preset is None:
                continue
            reasons = []
            if (
                preset.source != "desktop_sync"
                or preset.name != record["name"]
                or fingerprint(json.loads(preset.setting)) != record["hash"]
                or preset.preset_type
                != record.get("preset_type", {"machine": "printer"}.get(key.split("/")[0], key.split("/")[0]))
            ):
                reasons.append("Edited in farm manager")
            for other in existing:
                if other.id == preset.id:
                    continue
                setting = json.loads(other.setting)
                if (
                    other.inherits == preset.name
                    or setting.get("inherits") == preset.name
                    or (preset.preset_type == "printer" and preset.name in (setting.get("compatible_printers") or []))
                ):
                    reasons.append("Referenced by another profile")
                    break
            if preset.preset_type == "filament" and any(
                str(ref) == str(preset.id) or name == preset.name for ref, name in spool_refs
            ):
                reasons.append("Assigned to an inventory spool")
            if any(slot.preset_source == "local" and str(slot.preset_id) == str(preset.id) for slot in slots):
                reasons.append("Assigned to a printer slot")
            for pipeline in pipelines:
                refs = [
                    {"source": pipeline.printer_preset_source, "id": pipeline.printer_preset_id},
                    {"source": pipeline.process_preset_source, "id": pipeline.process_preset_id},
                    *json.loads(pipeline.filament_presets_json),
                ]
                if any(ref.get("source") == "local" and str(ref.get("id")) == str(preset.id) for ref in refs):
                    reasons.append("Used by a slicing pipeline")
                    break
            # A rename can create a new source key; keep copies still named by
            # a current desktop profile rather than treating them as deletions.
            if any(p.name == preset.name and p.preset_type == preset.preset_type for p in incoming):
                reasons.append("Still present on desktop under another key")
            preview.append(
                {"id": preset.id, "name": preset.name, "preset_type": preset.preset_type, "reasons": reasons}
            )
        removed = []
        if ids is not None:
            eligible = {p["id"] for p in preview if not p["reasons"]}
            if not ids or not set(ids).issubset(eligible):
                raise HTTPException(409, "The cleanup selection changed or is protected. Review a fresh preview.")
            now = datetime.now(timezone.utc).isoformat()
            _backup_profiles(state, existing, now)
            for preset_id in set(ids):
                preset = by_id[preset_id]
                removed.append(preset.name)
                await db.delete(preset)
            state["managed"] = {k: v for k, v in managed.items() if v["id"] not in set(ids)}
            state.setdefault("last_result", {})["missing"] = [
                v["name"] for k, v in state["managed"].items() if k not in keys
            ]
            row.value = json.dumps(state)
            await db.flush()
        return {"profiles": preview, "removed": removed}


class DesktopProfile(BaseModel):
    key: str = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=300)
    preset_type: str
    setting: dict
    legacy_id: int | None = None


def fingerprint(setting: dict) -> str:
    return hashlib.sha256(json.dumps(setting, sort_keys=True).encode()).hexdigest()


async def setting_row(db: AsyncSession, key: str):
    return (await db.execute(select(Settings).where(Settings.key == key))).scalar_one_or_none()


async def sync_status(db: AsyncSession) -> dict:
    row = await setting_row(db, STATE_KEY)
    state = json.loads(row.value) if row else {}
    result = dict(state.get("last_result", {"synced_at": None}))
    managed_ids = [v["id"] for v in state.get("managed", {}).values()]
    existing_ids = (
        set((await db.execute(select(LocalPreset.id).where(LocalPreset.id.in_(managed_ids)))).scalars().all())
        if managed_ids
        else set()
    )
    result["profile_count"] = len(existing_ids)
    # Missing means a retained copy needs review, not a historic source key
    # whose local row has already been deleted through the Profiles page.
    retained_names = {v["name"] for v in state.get("managed", {}).values() if v["id"] in existing_ids}
    result["missing"] = [name for name in result.get("missing", []) if name in retained_names]
    result["sidecar"] = {"status": "not_configured", "version": None}
    url_row = await setting_row(db, "orcaslicer_api_url")
    if os.environ.get("DESKTOP_PROFILE_SYNC_TOKEN") and url_row and url_row.value:
        try:
            async with httpx.AsyncClient(timeout=4, follow_redirects=False) as client:
                response = await client.get(url_row.value.rstrip("/") + "/health")
                response.raise_for_status()
                health = response.json()
                orca = health.get("checks", {}).get("orcaslicer", {})
                result["sidecar"] = {
                    "status": "connected"
                    if health.get("status") == "healthy" and orca.get("available")
                    else "unhealthy",
                    "version": orca.get("version"),
                }
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            result["sidecar"] = {"status": "unreachable", "version": None}
    return result


def _backup_profiles(state: dict, existing: list[LocalPreset], now: str) -> None:
    # Back up full settings before updating the transaction.
    backup = Path(settings.base_dir) / "backups" / "desktop-profiles"
    backup.mkdir(parents=True, exist_ok=True)
    (backup / (now.replace(":", "-") + ".json")).write_text(
        json.dumps(
            {
                "state": state,
                "presets": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "preset_type": p.preset_type,
                        "source": p.source,
                        "setting": json.loads(p.setting),
                    }
                    for p in existing
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


async def _desktop_profiles(db: AsyncSession) -> list[DesktopProfile]:
    token = os.environ.get("DESKTOP_PROFILE_SYNC_TOKEN")
    url_row = await setting_row(db, "orcaslicer_api_url")
    if not token or not url_row or not url_row.value:
        raise HTTPException(503, "Desktop profile sync is not configured on this host.")
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            response = await client.post(
                url_row.value.rstrip("/") + "/profiles/desktop-sync",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            profiles = [DesktopProfile.model_validate(p) for p in response.json()["profiles"]]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            502, "Could not sync desktop profiles. Check the Windows sidecar and saved profiles."
        ) from exc
    if not profiles or len(profiles) > 5000:
        raise HTTPException(502, "The sidecar returned an invalid profile count.")
    keys = set()
    names = set()
    for p in profiles:
        pair = (p.preset_type, p.name)
        if p.preset_type not in ("printer", "process", "filament") or p.key in keys or pair in names:
            raise HTTPException(502, "The sidecar returned duplicate or invalid profiles.")
        keys.add(p.key)
        names.add(pair)

    return profiles


async def sync_desktop_profiles(db: AsyncSession) -> dict:
    if sync_lock.locked():
        raise HTTPException(409, "A desktop profile sync is already running.")
    async with sync_lock:
        profiles = await _desktop_profiles(db)
        keys = {p.key for p in profiles}

        state_row = await setting_row(db, STATE_KEY)
        state = json.loads(state_row.value) if state_row else {"managed": {}}
        managed = state.get("managed", {})
        existing = list((await db.execute(select(LocalPreset))).scalars())
        by_id = {p.id: p for p in existing}
        by_name = {(p.preset_type, p.name): p for p in existing}
        now = datetime.now(timezone.utc).isoformat()
        result = {
            "added": 0,
            "updated": 0,
            "unchanged": 0,
            "conflicts": [],
            "missing": [],
            "changes": [],
            "synced_at": now,
        }
        _backup_profiles(state, existing, now)
        for incoming in profiles:
            record = managed.get(incoming.key)
            if record is None:
                # Older companions keyed by a relative filename (including
                # subfolders); merged sources key by type/name. Rebind only
                # a unique managed row, preserving its ID and original hash
                # so genuine farm-manager edits still trigger a conflict.
                previous = [
                    (key, value)
                    for key, value in managed.items()
                    if value["name"] == incoming.name
                    and value["id"] in by_id
                    and by_id[value["id"]].name == incoming.name
                    and by_id[value["id"]].preset_type == incoming.preset_type
                    and by_id[value["id"]].source == "desktop_sync"
                ]
                if len(previous) == 1:
                    old_key, record = previous[0]
                    del managed[old_key]
                    managed[incoming.key] = record
            preset = by_id.get(record["id"]) if record else None
            if preset and (
                fingerprint(json.loads(preset.setting)) != record["hash"]
                or preset.name != record["name"]
                or preset.preset_type != incoming.preset_type
            ):
                result["conflicts"].append(incoming.name)
                continue
            if not record:
                # Adopt only IDs recorded by this host's earlier CLI sync.
                candidate = by_id.get(incoming.legacy_id)
                if candidate and candidate.name == incoming.name and candidate.preset_type == incoming.preset_type:
                    preset = candidate
                elif (incoming.preset_type, incoming.name) in by_name:
                    result["conflicts"].append(incoming.name)
                    continue
            collision = by_name.get((incoming.preset_type, incoming.name))
            if collision and collision is not preset:
                result["conflicts"].append(incoming.name)
                continue
            if preset is None:
                preset = LocalPreset(name=incoming.name, preset_type=incoming.preset_type, source="desktop_sync")
                db.add(preset)
                result["added"] += 1
                action = "added"
            elif json.loads(preset.setting) == incoming.setting and preset.name == incoming.name:
                result["unchanged"] += 1
                action = "unchanged"
            else:
                result["updated"] += 1
                action = "updated"
            result["changes"].append({"name": incoming.name, "preset_type": incoming.preset_type, "action": action})
            preset.name = incoming.name
            preset.source = "desktop_sync"
            preset.setting = json.dumps(incoming.setting)
            preset.inherits = incoming.setting.get("inherits")
            preset.version = incoming.setting.get("version")
            for key in (
                "filament_type",
                "filament_vendor",
                "nozzle_temp_min",
                "nozzle_temp_max",
                "pressure_advance",
                "default_filament_colour",
                "filament_cost",
                "filament_density",
                "compatible_printers",
            ):
                setattr(preset, key, None)
            for key, value in extract_core_fields(incoming.setting).items():
                setattr(preset, key, value)
            await db.flush()
            managed[incoming.key] = {
                "id": preset.id,
                "name": incoming.name,
                "preset_type": incoming.preset_type,
                "hash": fingerprint(incoming.setting),
            }
        managed = {k: v for k, v in managed.items() if k in keys or v["id"] in by_id}
        result["missing"] = [v["name"] for k, v in managed.items() if k not in keys]
        state = {"managed": managed, "last_result": result}
        if state_row is None:
            state_row = Settings(key=STATE_KEY)
            db.add(state_row)
        state_row.value = json.dumps(state)
        await db.flush()
        return result

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
    result["profile_count"] = (
        len((await db.execute(select(LocalPreset.id).where(LocalPreset.id.in_(managed_ids)))).scalars().all())
        if managed_ids
        else 0
    )
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


async def sync_desktop_profiles(db: AsyncSession) -> dict:
    if sync_lock.locked():
        raise HTTPException(409, "A desktop profile sync is already running.")
    async with sync_lock:
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
        for incoming in profiles:
            record = managed.get(incoming.key)
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
            managed[incoming.key] = {"id": preset.id, "name": incoming.name, "hash": fingerprint(incoming.setting)}
        result["missing"] = [v["name"] for k, v in managed.items() if k not in keys]
        state = {"managed": managed, "last_result": result}
        if state_row is None:
            state_row = Settings(key=STATE_KEY)
            db.add(state_row)
        state_row.value = json.dumps(state)
        await db.flush()
        return result

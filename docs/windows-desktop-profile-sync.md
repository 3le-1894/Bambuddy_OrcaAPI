# Windows desktop profile sync

This fork adds **Profiles â†’ Local Profiles â†’ Sync desktop Orca profiles**.
Save changes in desktop Orca, then click the button. Bambuddy requests the
saved profiles from the Windows Orca API companion, imports new profiles,
updates managed profiles, and refreshes the slicing dropdowns. The page
shows the last sync time and the added, updated, and unchanged counts.

The **Desktop Orca profiles** panel checks the companion connection and shows
its Orca version and the number of managed profiles still present in Bambuddy.
**Sync now** is enabled once the companion is connected. Use the refresh icon
to recheck a stopped or restarted companion. Connection status refreshes every
30 seconds while the page is active.

**View last changes** lists updated, added, and unchanged profile names from
the last successful sync, plus conflicts and missing profiles. Results persist
after reopening the page. **Review** expands the retained-profile details when
conflicts or missing profiles need attention; it does not overwrite those edits.

Profiles edited directly in Bambuddy after a sync are preserved and reported
as conflicts. Removed or renamed desktop profiles are reported and kept.
Unrelated profiles with the same name are preserved. The first sync can adopt
profiles whose IDs are recorded in the companion's earlier CLI sync manifest.

Every sync backs up Bambuddy profile settings under
`data/bambuddy/backups/desktop-profiles`. Sidecar snapshots are backed up under
`OrcaSlicer_API/data/profile-sync-backups`. Updates use a database transaction.
The sidecar resolves the entire desktop collection before writing snapshots.

## Companion configuration

The companion changes are preserved in this fork as
`deploy/orca-sidecar.patch`. It applies to upstream OrcaSlicer API commit
`6b85ecc29344443149c86af1f53dbf1095017b74` from
`https://github.com/maziggy/orca-slicer-api.git`. The patch includes the Windows
service scripts, profile identity fixes, authenticated sync endpoint, and tests.
No runtime binaries, saved desktop profiles, or authentication tokens are included.

To restore the companion in a **new, empty installation directory**, clone
that repository, check out the pinned commit, and apply the patch:

```powershell
git clone https://github.com/maziggy/orca-slicer-api.git C:\Bambuddy\OrcaSlicer_API
git -C C:\Bambuddy\OrcaSlicer_API checkout 6b85ecc29344443149c86af1f53dbf1095017b74
git -C C:\Bambuddy\OrcaSlicer_API apply C:\Bambuddy\Bambuddy_OrcaAPI\deploy\orca-sidecar.patch
```

Then follow the patched companion's `README.windows.md`: install its npm
dependencies, snapshot the compiled desktop Orca build with
`scripts/build-windows.ps1`, and register `scripts/install-windows.ps1`.
The deployment script requires that companion runtime to be installed first.
The deployment script's default desktop account folder is specific to this PC;
adjust it when installing under a different Windows or Orca account.

The Windows Orca API requires `DESKTOP_PROFILES_PATH` pointing to the saved
Orca account folder and `DESKTOP_PROFILE_SYNC_TOKEN` with a shared secret.
If Orca is launched with `--datadir`, choose the account folder under that
instance's `user` directory, rather than the old Roaming folder. For example,
the Vulcan instance on this PC saves under
`C:\Users\3leme\OrcaProfiles\Vulcan\user\5e5d6945-094f-439c-bad1-a5399dcb4267`.
Pass `-DesktopProfilesPath` to `deploy/update-windows-fork.ps1` when selecting
the source. Later deployments preserve this configured path. For multiple instances, pass `-DesktopProfilesPaths` with an array of account
folders. The sidecar stores this as `DESKTOP_PROFILES_PATHS`, a JSON array. It
merges profiles by type and exact name, choosing the source file with the newest
modification time. Equal timestamps use the first configured source. Different
names remain separate profiles; `_Tuned` does not implicitly retire an older
name. File copies can change timestamps, so this compares file modification
times rather than a semantic revision number. All configured folders must scan
successfully before snapshots are written or cleanup can run.

Deleting an imported copy from the farm manager does not exclude a profile
still present in the configured desktop source. The next sync imports it again.
Delete the source desktop profile to retire it, then use the cleanup preview to
remove its retained farm-manager copy. A profile held only in a project must be
saved as a user preset before it can appear in desktop sync.

Bambuddy receives the same token through `desktop-sync.env`; its configured
Orca API URL is used for the request. The browser never receives the token
and cannot submit arbitrary filesystem paths. Sync requires Bambuddy's
settings update permission when authentication is enabled.

## Removing profiles deleted from desktop Orca

In the desktop sync panel, choose **Remove profiles deleted locally**. The
sidecar scans the saved desktop collection again and the panel previews only
previously managed profiles now absent from that collection. Select the copies
to remove and choose **Remove selected copies**. Sync now continues to preserve
missing copies until this cleanup is explicitly requested.

Cleanup protects profiles edited in Bambuddy, profiles referenced by other local
presets, inventory spools (including per-model overrides), printer slots, and
slicing pipelines. Clear or replace those references before retrying cleanup.
The server repeats these checks when applying the selection; stale or protected
selections are rejected without deleting anything. A full preset/settings backup
is written under `data/backups/desktop-profiles` before removal.

An unavailable folder, failed sidecar response, invalid collection, or empty
collection prevents cleanup. In particular, deleting every desktop custom profile
requires manual cleanup because an empty scan is not accepted as deletion evidence.
This action removes Bambuddy's imported database copies. It does not delete desktop
files, bundled presets, or the sidecar's cached snapshot files.

## Build and deploy

On this installation, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Bambuddy\Update-BambuddyFork.ps1"
```

The launcher calls the versioned script `deploy/update-windows-fork.ps1`.
It builds the local checkout while Bambuddy remains available, compiles the
companion, stops Bambuddy for a consistent full data backup, configures the
shared token, restarts the companion, and deploys the fork. It checks both
service health and the new sync route. Failed deployments restore the previous
image, configuration, and backed-up data; the failed data is retained separately.
The previous image is tagged for rollback and a timestamped backup records
both image identities. Keep backup folders private because they include
application credentials and the companion token.

Use `-BuildOnly` to build without deployment. Docker Desktop must be running.
The script builds the existing checkout; it does not fetch, merge, or push Git
changes. Upstream updates and merge conflicts need to be handled separately.
Do not use the official image's pull/update workflow for this fork.

Desktop profile sync does not update the Orca executable or bundled resource
tree. Refresh the sidecar runtime separately after building a new desktop Orca.

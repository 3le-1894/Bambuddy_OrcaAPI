# Windows desktop profile sync

This fork adds **Profiles → Local Profiles → Sync desktop Orca profiles**.
Save changes in desktop Orca, then click the button. Bambuddy requests the
saved profiles from the Windows Orca API companion, imports new profiles,
updates managed profiles, and refreshes the slicing dropdowns. The page
shows the last sync time and the added, updated, and unchanged counts.

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
Bambuddy receives the same token through `desktop-sync.env`; its configured
Orca API URL is used for the request. The browser never receives the token
and cannot submit arbitrary filesystem paths. Sync requires Bambuddy's
settings update permission when authentication is enabled.

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

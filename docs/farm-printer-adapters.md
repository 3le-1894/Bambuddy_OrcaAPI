# Farm printer adapter foundation

This is the first backend step toward a mixed Bambu, Klipper, and Duet/RRF
fleet. It does not add non-Bambu connections or change the dashboard yet.

## Contract

`backend/app/services/printer_adapter.py` defines an immutable status snapshot,
temperature readings, adapter capabilities, and an async control interface.
Progress is percent (0–100), durations are seconds, temperatures are Celsius,
and `last_seen_at` is a UTC Unix timestamp of inbound communication (not the
time the dashboard asked for status). Missing temperatures are `None`, not zero.
Temperature identifiers are adapter-local; the Bambu adapter preserves
`nozzle` and `nozzle_2` without inventing tool numbering for other vendors.

Connection, activity, and readiness are separate. Unknown activity must not
become ready just because the transport is connected. Offline activity can
retain a native state and last-known telemetry for diagnosis; callers must
check connection before treating those readings as current. Completed means
the firmware reported completion, not that an operator inspected the part.

The adapter reports transport-level readiness. The manager overlays its
persisted plate-clearance gate using `get_fleet_status`; dashboard consumers
must use this method rather than reading adapter readiness directly.

## Bambu integration

`BambuPrinterAdapter` wraps the existing manager-owned MQTT client. It preserves
the existing reconnect, callback, command, and legacy status paths. No second
MQTT connection is created. The manager constructs wrappers on access so new
lookups after reconnect wrap the replacement client. Do not retain adapter
handles across reconnects; request them from the manager when needed.

New manager entry points:

- `get_adapter(printer_id)` — common adapter, or `None` if unregistered.
- `get_fleet_status(printer_id)` — common status including plate clearance.
- `get_all_fleet_statuses()` — snapshots of currently registered connections.

Pause/resume/cancel are async and offload the existing synchronous MQTT command
submission to a worker thread. A `True` result means the existing client accepted
the submission, not that the printer confirmed the physical operation.

Capabilities describe operations implemented by the common interface. Upload,
start, lifecycle, and event subscription contracts will be added with the first
new transport. Existing Bambu upload/start routes remain in place.

## Next steps

### Background refresh and cached reads

Common `get_status()` calls now have an explicit no-I/O contract. The Bambu
wrapper reads MQTT telemetry and evaluates local freshness without invoking
session recovery. Legacy Bambu status APIs retain their existing behavior.

`PollingPrinterAdapter` provides the base for future HTTP transports. It runs
one asynchronous refresh task, returns immutable cached snapshots, imposes a
per-refresh deadline, and retries failures with capped exponential backoff.
Failures and expired samples show offline readiness while retaining last-known
telemetry and its original communication timestamp for diagnosis. Successful
refresh resets backoff. Reads never schedule a poll or reconnect.

Subclasses must use async HTTP with explicit network timeouts and propagate
cancellation. Object discovery should be cached by the transport and repeated
on connection/configuration changes. The base does not yet implement Moonraker
HTTP or WebSocket subscriptions. A subclass that owns an HTTP client must close
it in `stop()` after awaiting the base task cancellation.

New lifecycle entry points are `register_fleet_adapter`,
`unregister_fleet_adapter`, and `shutdown_fleet_adapters`. Registration starts
background work without waiting for a network connection. Replacement awaits
the old adapter's shutdown. New transports are kept outside the legacy Bambu
client registry; lifecycle changes must use these async methods, not the legacy
`disconnect_printer`/`disconnect_all` methods. Application shutdown now awaits
managed adapters before disconnecting Bambu clients.

This is infrastructure for the first Moonraker adapter. It does not add a new
printer connection choice or change the existing dashboard yet.

Add connection configuration and database migrations, a Moonraker adapter,
then Duet standalone/SBC adapters. Add fleet API schemas and WebSocket events
before switching dashboard consumers. Retain legacy Bambu state for AMS and
other vendor-specific features; do not force other adapters to fabricate it.

## Connection configuration patch

The Add Printer form now offers Bambu, Klipper/Moonraker and Duet/RRF. Existing
Bambu records keep their IDs, credentials and related history. HTTP connections
store a server URL (including an optional port/proxy prefix), authentication
mode and, for Duet, standalone or SBC mode. Moonraker supports no-auth/API-key
configuration initially; Duet supports no-auth/password configuration.

Non-Bambu records do not receive invented serial numbers. Their Bambu-only
columns are NULL, and the existing database ID remains their identity. Legacy
display fields are empty strings in responses for compatibility with existing
UI consumers. Connection type is fixed after creation to avoid reclassifying
historical records; create a separate configuration to change printer families.

Credentials are input-only. Blank edits omit the credential to preserve the
saved value; choosing no authentication clears it. Normal and privileged
printer responses expose only `has_connection_secret`, never the new secret.
New secrets use the application's encryption key and refuse plaintext fallback
if secure storage is unavailable. Preserve the existing encryption key with
database backups, just as for other encrypted application settings.

Klipper and Duet entries stay inactive and appear as configuration-only cards
with edit/remove actions. Legacy control, file and connection endpoints return
409 for them. They are not eligible for active-printer startup or scheduling.
The transport implementation is a separate patch; saved settings do not imply
that a printer was contacted or verified.

The migration adds columns and relaxes Bambu-only NOT NULL requirements.
PostgreSQL uses ALTER COLUMN; SQLite follows the existing project's schema
editing approach with candidate SQL validation and a schema-version bump.
It does not rebuild/drop the printer table. Tests verify IDs, foreign-key
relationships, indexes, integrity, repeat migration and separate endpoints on
one host. PostgreSQL DDL is checked with mocks, not a live PostgreSQL server.

Build/deploy manually after reviewing the patch:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Bambuddy\Update-BambuddyFork.ps1"
```

## Verification

Focused tests are in `backend/tests/unit/test_printer_adapter.py` and can be run
with the existing backend test environment. No build or deployment is required
for this source change.

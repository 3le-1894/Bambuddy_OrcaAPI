# Farm printer adapter foundation

This is the first backend step toward a mixed Bambu, Klipper, and Duet/RRF
fleet. Moonraker monitoring now uses this foundation; Duet monitoring and
non-Bambu print dispatch remain future work.

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

## Background refresh and lifecycle

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
on connection/configuration changes. The base is transport-independent; the
Moonraker subclass adds HTTP polling. A subclass that owns an HTTP client must close
it in `stop()` after awaiting the base task cancellation.

New lifecycle entry points are `register_fleet_adapter`,
`unregister_fleet_adapter`, and `shutdown_fleet_adapters`. Registration starts
background work without waiting for a network connection. Replacement awaits
the old adapter's shutdown. New transports are kept outside the legacy Bambu
client registry; lifecycle changes must use these async methods, not the legacy
`disconnect_printer`/`disconnect_all` methods. Application shutdown now awaits
managed adapters before disconnecting Bambu clients.

Duet standalone/SBC adapters, file upload/start and WebSocket subscriptions
remain future work. Retain legacy Bambu state for AMS and other vendor-specific
features; do not force other adapters to fabricate it.

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

Duet entries stay inactive and appear as configuration-only cards. Klipper
entries now support monitoring as described below. Legacy control, file and
connection endpoints return 409 for both; use configuration edits to enable or
disable Moonraker monitoring. No non-Bambu print dispatch is implemented.

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

## Moonraker monitoring patch

`MoonrakerPrinterAdapter` uses one async HTTP client per active Klipper entry,
with the saved API key in `X-Api-Key` when configured. It preserves proxy URL
prefixes and does not follow redirects or use environment proxies. Background
monitoring never sends print commands. The background poll checks `/server/info`, discovers available
objects once, then queries their status every five seconds. Object discovery
is repeated after failures or Klipper restarts. Timeouts and retry/backoff come
from the polling base. Stopping/replacing/removing the adapter awaits its task
and closes its HTTP client. Credential failures keep the printer offline.

State mapping is conservative: standby is idle/ready, printing and paused are
busy, complete awaits physical plate clearance, cancelled is idle/blocked,
and errors/shutdown are blocked. A responding Moonraker with disconnected
Klipper is distinct from an unreachable Moonraker. Missing or unexpected data
never implies readiness. Available extruders are named with their Klipper
identifiers; unavailable measurements remain null, and zero remains valid.
Only `temperature_sensor chamber` or `heater_generic chamber` is shown as a
chamber sensor. Pi/MCU sensors are never substituted.

Remaining time is explicitly an estimate from print duration and file progress,
not slicer metadata. It is absent with no usable progress/duration or while
paused. New Klipper entries enable monitoring automatically without requiring
connectivity during save. Older configuration-only Klipper entries retain their
inactive setting: edit them and uncheck **Disable monitoring** to enable it.

`GET /api/v1/printers/fleet-status` requires printers:read and returns cached
common status plus family and capabilities for configured printers. It does no
printer network I/O and returns no connection secrets. The dashboard shares one
five-second request across all Klipper cards. Status summary, filters, grouping
and ETA sorting use a display-only projection of these snapshots; legacy Bambu
caches and command routes remain separate. Failed backend requests or offline
snapshots hide stale temperature/progress readings on the Klipper card.

Monitoring is tested with httpx MockTransport, not a physical printer. Tests
cover state/unit mapping, multi-extruder readings, malformed responses, auth
headers, proxy paths, cache-only reads, discovery reuse, timeout cancellation,
HTTP resource cleanup, settings lifecycle and dashboard rendering. Live testing
requires a Pi with Klipper and Moonraker reachable from the Bambuddy container.
Print upload/start, cameras, print archiving, Moonraker WebSockets and Duet
monitoring remain future work. Pause/resume/cancel controls are described below.

API references: [Moonraker printer administration](https://moonraker.readthedocs.io/en/latest/external_api/printer/),
[printer objects](https://moonraker.readthedocs.io/en/latest/printer_objects/),
and [HTTP API authentication](https://moonraker.readthedocs.io/en/latest/external_api/introduction/).

## Moonraker print controls

The Klipper card offers Pause while printing, Resume while paused, and Cancel
print for either state. Cancel requires a separate confirmation that names the
file. Changing files, losing connectivity, or losing control permission clears
the prompt. Unsupported operations, inactive monitoring, stale/unavailable
status, non-busy readiness and Klipper errors do not offer actionable buttons.
Buttons are disabled during submission. API failures appear on the card and
are not automatically retried. Success means Moonraker acknowledged submission;
only subsequent telemetry confirms the physical state.

`POST /api/v1/printers/{printer_id}/fleet-control/{action}` accepts pause, resume
or cancel with JSON `expected_filename` and, for cancellation, `confirmed:true`.
It requires printers:control and enforces an API key's per-printer allowlist.
The endpoint currently accepts only Klipper entries. Legacy Bambu endpoints
continue using their existing code, and Duet remains configuration-only.

The route checks cached status and capabilities. The adapter then checks fresh
Moonraker status and the filename again before sending one dedicated
`/printer/print/pause`, `/resume`, or `/cancel` POST. This checks state and
filename; Moonraker does not provide an atomic compare-and-command transaction,
so it cannot distinguish two successive jobs with the same filename in every
case. Capabilities require the discovered print_stats, virtual_sdcard and
pause_resume objects. No raw G-code command endpoint is added.

HTTP polls and commands share an I/O lock. Only one command is accepted at a
time; concurrent submissions receive a conflict instead of accumulating a
command queue. After acknowledgment or an uncertain transport outcome, another
command is blocked until telemetry shows its result, a different filename, a
terminal state, or 60 seconds have passed. Commands have a total request
deadline and are never automatically retried. Timeouts return 504 with an
unknown-outcome message; malformed acknowledgments and transport failures
return safe 502 messages. After uncertain failures, the snapshot is offline
until monitoring refreshes it. Shutdown marks the adapter as stopping, rejects
new commands and awaits any in-flight request before closing the HTTP client.

Tests use MockTransport and temporary databases. They cover authenticated proxy
paths, allowed/forbidden states, changed files, missing capabilities, duplicate
submissions, deadline cancellation, safe errors, shutdown behavior, permissions,
API-key printer scope, and UI confirmation/error flows. No commands were sent
to a physical printer during development. Physical verification still requires
a configured Klipper printer connected to Athena.

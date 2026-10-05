import type { FleetPrinterStatus, FleetTemperature } from '../api/client';

function temperature(value: FleetTemperature) {
  return `${value.current == null ? '—' : Math.round(value.current)}°C${value.target == null ? '' : ` / ${Math.round(value.target)}°C`}`;
}

export function FleetTelemetry({ status, active, failed = false }: {
  status?: FleetPrinterStatus; active: boolean; failed?: boolean;
}) {
  if (!active) return <p className="text-sm text-amber-400 mt-2">Monitoring disabled — enable it in Edit configuration.</p>;
  if (failed) return <p className="text-sm text-amber-400 mt-2">Status unavailable — cannot reach Bambuddy.</p>;
  if (!status?.connected) return <p className="text-sm text-amber-400 mt-2">Offline — check Moonraker address, authentication, and network access.</p>;
  const current = status.activity !== 'offline';
  return <div className="mt-3 space-y-2" aria-label="Printer telemetry">
    <p className="text-sm">{current ? 'Connected' : 'Moonraker connected; Klipper unavailable'} · {status.native_state}</p>
    <p className="text-sm text-bambu-gray">{status.readiness === 'awaiting_clearance'
      ? 'Print complete — check and clear the build plate before starting another print.'
      : `Readiness: ${status.readiness}`}</p>
    {status.filename && <p className="text-sm break-all">{status.filename}</p>}
    {current && status.progress_percent != null && <div>
      <progress className="w-full" max={100} value={status.progress_percent} aria-label="Print progress" />
      <p className="text-sm">{Math.round(status.progress_percent)}%</p>
    </div>}
    {current && status.remaining_seconds != null && status.activity === 'printing' &&
      <p className="text-sm text-bambu-gray">Estimated remaining: {Math.ceil(status.remaining_seconds / 60)} min</p>}
    {current && <div className="grid grid-cols-2 gap-2 text-sm">
      {status.tools.map(tool => <p key={tool.identifier}>{tool.identifier}: {temperature(tool)}</p>)}
      {status.bed && <p>Bed: {temperature(status.bed)}</p>}
      {status.chamber && <p>Chamber: {temperature(status.chamber)}</p>}
    </div>}
    <p className="text-xs text-bambu-gray">Monitoring only. Print controls are not yet available.</p>
  </div>;
}

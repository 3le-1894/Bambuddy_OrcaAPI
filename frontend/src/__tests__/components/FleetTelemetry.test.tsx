import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import type { FleetPrinterStatus, Printer } from '../../api/client';
import { FleetTelemetry } from '../../components/FleetTelemetry';
import { fleetDashboardStatus } from '../../utils/fleetDashboardStatus';

const status: FleetPrinterStatus = {
  family: 'klipper', connected: true, activity: 'printing', readiness: 'busy', native_state: 'printing',
  filename: 'part.gcode', progress_percent: 25, remaining_seconds: 360, last_seen_at: 1234,
  tools: [{ identifier: 'extruder', current: 0, target: 210 }, { identifier: 'extruder1', current: 22, target: null }],
  bed: { identifier: 'heater_bed', current: 60, target: 60 }, chamber: null,
  error_codes: [], capabilities: { pause: false, resume: false, cancel: false },
};

describe('Klipper telemetry', () => {
  it('shows actual tools, zero temperatures, progress and an explicitly estimated remaining time', () => {
    render(<FleetTelemetry active status={status} />);
    expect(screen.getByText('extruder: 0°C / 210°C')).toBeInTheDocument();
    expect(screen.getByText('extruder1: 22°C')).toBeInTheDocument();
    expect(screen.getByLabelText('Print progress')).toHaveAttribute('value', '25');
    expect(screen.getByText('Estimated remaining: 6 min')).toBeInTheDocument();
    expect(screen.queryByText(/Chamber:/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /pause|resume|cancel/i })).not.toBeInTheDocument();
  });

  it('hides retained temperatures and progress when offline or the backend request fails', () => {
    const { rerender } = render(<FleetTelemetry active status={{ ...status, connected: false }} />);
    expect(screen.getByText(/Offline/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Print progress')).not.toBeInTheDocument();
    expect(screen.queryByText(/extruder:/)).not.toBeInTheDocument();
    rerender(<FleetTelemetry active failed status={status} />);
    expect(screen.getByText(/cannot reach Bambuddy/)).toBeInTheDocument();
    expect(screen.queryByText(/extruder:/)).not.toBeInTheDocument();
  });

  it('shows monitoring disabled and completed plate-clearance guidance', () => {
    const { rerender } = render(<FleetTelemetry active={false} status={status} />);
    expect(screen.getByText(/Monitoring disabled/)).toBeInTheDocument();
    rerender(<FleetTelemetry active status={{ ...status, activity: 'completed', readiness: 'awaiting_clearance' }} />);
    expect(screen.getByText(/check and clear the build plate/)).toBeInTheDocument();
  });

  it('projects cached fleet status for filters without overwriting legacy Bambu cache', () => {
    const client = new QueryClient();
    const printer = { id: 10, connection_type: 'klipper', is_active: true } as Printer;
    client.setQueryData(['fleetStatuses'], { 10: status });
    expect(fleetDashboardStatus(client, printer)).toMatchObject({ state: 'RUNNING', remaining_time: 6, progress: 25 });
    expect(client.getQueryData(['printerStatus', 10])).toBeUndefined();
    expect(fleetDashboardStatus(client, { ...printer, is_active: false })?.connected).toBe(false);
    client.setQueryData(['fleetStatuses'], { 10: { ...status, activity: 'error' } });
    expect(fleetDashboardStatus(client, printer)?.fleet_error).toBe(true);
  });
});

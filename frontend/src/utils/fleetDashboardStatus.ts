import type { QueryClient } from '@tanstack/react-query';
import type { FleetPrinterStatus, Printer, PrinterStatus } from '../api/client';

// Display projection only. Do not use this to send legacy Bambu controls.
export function fleetDashboardStatus(client: QueryClient, printer: Printer) {
  if (!printer.connection_type || printer.connection_type === 'bambu') {
    return client.getQueryData<Pick<PrinterStatus, 'connected' | 'state' | 'remaining_time' | 'progress' | 'hms_errors'> & { fleet_error?: boolean }>(['printerStatus', printer.id]);
  }
  const query = client.getQueryState(['fleetStatuses']);
  const status = client.getQueryData<Record<number, FleetPrinterStatus>>(['fleetStatuses'])?.[printer.id];
  return {
    connected: printer.is_active && query?.status !== 'error' && !!status?.connected && status.activity !== 'offline',
    state: status ? ({ printing: 'RUNNING', paused: 'PAUSE', completed: 'FINISH', error: 'FAILED',
      idle: 'IDLE', unknown: null, offline: null, preparing: 'PREPARE' } as const)[status.activity] : null,
    remaining_time: status?.remaining_seconds == null ? null : status.remaining_seconds / 60,
    progress: status?.progress_percent ?? null,
    fleet_error: status?.activity === 'error',
    hms_errors: undefined,
  };
}

import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api, type FleetPrintAction, type FleetPrinterStatus } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { Button } from './Button';

export function FleetPrintControls({ printerId, status, active, failed = false }: {
  printerId: number; status?: FleetPrinterStatus; active: boolean; failed?: boolean;
}) {
  const { hasPermission } = useAuth();
  const client = useQueryClient();
  const [confirmFor, setConfirmFor] = useState<string | null>(null);
  const permitted = hasPermission('printers:control');
  const available = permitted && active && !failed && status?.family === 'klipper'
    && status.connected && status.readiness === 'busy' && !!status.filename;
  const canPause = available && status?.capabilities.pause && status.activity === 'printing';
  const canResume = available && status?.capabilities.resume && status.activity === 'paused';
  const canCancel = available && status?.capabilities.cancel && ['printing', 'paused'].includes(status.activity);
  const command = useMutation({
    mutationFn: ({ action, filename }: { action: FleetPrintAction; filename: string }) =>
      api.controlFleetPrint(printerId, action, filename, action === 'cancel'),
    onSuccess: () => {
      setConfirmFor(null);
      client.invalidateQueries({ queryKey: ['fleetStatuses'] });
    },
    onError: () => client.invalidateQueries({ queryKey: ['fleetStatuses'] }),
  });
  useEffect(() => {
    if (!canCancel || confirmFor !== status?.filename) setConfirmFor(null);
  }, [canCancel, confirmFor, status?.filename]);
  const send = (action: FleetPrintAction) => {
    if (!status?.filename || command.isPending) return;
    command.mutate({ action, filename: status.filename });
  };
  if (!permitted) return null;
  return <div className="mt-3 space-y-2" aria-label="Klipper print controls">
    <div className="flex flex-wrap gap-2">
      {canPause && <Button disabled={command.isPending || confirmFor !== null} onClick={() => send('pause')}>Pause</Button>}
      {canResume && <Button disabled={command.isPending || confirmFor !== null} onClick={() => send('resume')}>Resume</Button>}
      {canCancel && <Button variant="danger" disabled={command.isPending} onClick={() => {
        command.reset(); setConfirmFor(status!.filename);
      }}>Cancel print</Button>}
    </div>
    {!canPause && !canResume && !canCancel && <p className="text-xs text-bambu-gray">Print controls are unavailable in this state.</p>}
    {confirmFor !== null && canCancel && confirmFor === status?.filename && <div className="rounded-lg border border-red-500/40 p-3 space-y-2">
      <p className="text-sm break-all">Cancel {confirmFor}? This will stop the current print.</p>
      <div className="flex gap-2">
        <Button variant="danger" disabled={command.isPending} onClick={() => send('cancel')}>Confirm cancellation</Button>
        <Button variant="secondary" disabled={command.isPending} onClick={() => setConfirmFor(null)}>Keep printing</Button>
      </div>
    </div>}
    {command.isPending && <p role="status" className="text-sm text-bambu-gray">Sending command…</p>}
    {command.isSuccess && <p role="status" className="text-sm text-bambu-gray">Command accepted. Printer status updates will confirm the result.</p>}
    {command.isError && <p role="alert" className="text-sm text-red-400">{command.error.message}</p>}
  </div>;
}

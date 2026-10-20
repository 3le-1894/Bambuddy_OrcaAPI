import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type FleetPrinterStatus } from '../../api/client';
import { FleetPrintControls } from '../../components/FleetPrintControls';

const permission = vi.hoisted(() => ({ allowed: true }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ hasPermission: () => permission.allowed }) }));

const status: FleetPrinterStatus = {
  family: 'klipper', connected: true, activity: 'printing', readiness: 'busy', native_state: 'printing',
  filename: 'part.gcode', progress_percent: 25, remaining_seconds: 360, last_seen_at: Date.now() / 1000,
  tools: [], bed: null, chamber: null, error_codes: [], capabilities: { pause: true, resume: true, cancel: true },
};

function setup(next = status, active = true, failed = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  const ui = (value: FleetPrinterStatus) => <QueryClientProvider client={client}>
    <FleetPrintControls printerId={10} status={value} active={active} failed={failed} />
  </QueryClientProvider>;
  return { ...render(ui(next)), ui };
}

beforeEach(() => {
  vi.restoreAllMocks();
  permission.allowed = true;
  vi.spyOn(api, 'controlFleetPrint').mockResolvedValue({ success: true, message: 'Accepted' });
});

describe('Klipper print controls', () => {
  it('offers Pause during printing and Resume when paused', async () => {
    const user = userEvent.setup();
    const { rerender, ui } = setup();
    expect(screen.queryByRole('button', { name: 'Resume' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Pause' }));
    await waitFor(() => expect(api.controlFleetPrint).toHaveBeenCalledWith(10, 'pause', 'part.gcode', false));
    rerender(ui({ ...status, activity: 'paused' }));
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(api.controlFleetPrint).toHaveBeenCalledWith(10, 'resume', 'part.gcode', false));
  });

  it('requires explicit confirmation to cancel and allows keeping the print', async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole('button', { name: 'Cancel print' }));
    expect(api.controlFleetPrint).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Keep printing' }));
    expect(screen.queryByRole('button', { name: 'Confirm cancellation' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel print' }));
    await user.click(screen.getByRole('button', { name: 'Confirm cancellation' }));
    await waitFor(() => expect(api.controlFleetPrint).toHaveBeenCalledWith(10, 'cancel', 'part.gcode', true));
    expect(api.controlFleetPrint).toHaveBeenCalledTimes(1);
  });

  it('dismisses cancellation confirmation when the job changes', async () => {
    const user = userEvent.setup();
    const { rerender, ui } = setup();
    await user.click(screen.getByRole('button', { name: 'Cancel print' }));
    rerender(ui({ ...status, filename: 'new.gcode' }));
    expect(screen.queryByRole('button', { name: 'Confirm cancellation' })).not.toBeInTheDocument();
    expect(api.controlFleetPrint).not.toHaveBeenCalled();
  });

  it.each(['offline', 'error', 'idle', 'disabled', 'unsupported', 'failed', 'viewer'])('does not offer controls for %s', caseName => {
    let next = status;
    if (caseName === 'offline') next = { ...status, connected: false };
    if (caseName === 'error' || caseName === 'idle') next = { ...status, activity: caseName, readiness: 'blocked' };
    if (caseName === 'unsupported') next = { ...status, capabilities: { pause: false, resume: false, cancel: false } };
    if (caseName === 'viewer') permission.allowed = false;
    setup(next, caseName !== 'disabled', caseName === 'failed');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(api.controlFleetPrint).not.toHaveBeenCalled();
  });

  it('disables buttons during submission and displays errors without retrying', async () => {
    const user = userEvent.setup();
    let reject!: (reason: Error) => void;
    vi.mocked(api.controlFleetPrint).mockReturnValue(new Promise((_resolve, rejectPromise) => { reject = rejectPromise; }));
    setup();
    await user.click(screen.getByRole('button', { name: 'Pause' }));
    expect(screen.getByRole('button', { name: 'Pause' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel print' })).toBeDisabled();
    reject(new Error('Outcome unknown; check printer state before retrying'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Outcome unknown');
    expect(api.controlFleetPrint).toHaveBeenCalledTimes(1);
  });
});

import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { AddPrinterModal } from '../../pages/PrintersPage';
import { PrinterConnectionFields } from '../../components/PrinterConnectionFields';
import { connectionPayload } from '../../utils/printerConnection';

describe('Printer connection configuration', () => {
  it('saves Moonraker configuration without Bambu credentials or diagnostics', async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<AddPrinterModal onClose={vi.fn()} onAdd={onAdd} existingSerials={[]} />);
    await user.selectOptions(screen.getByLabelText('Connection type'), 'klipper');
    expect(screen.queryByPlaceholderText('01P00A000000000')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('From printer settings')).not.toBeInTheDocument();
    await user.type(screen.getByPlaceholderText('My Printer'), 'Klipper test');
    await user.type(screen.getByLabelText('Server URL'), 'http://pi.local:7125');
    await user.click(screen.getByRole('button', { name: /^add printer$/i }));
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({
      connection_type: 'klipper', api_url: 'http://pi.local:7125',
      auth_mode: 'none', serial_number: '', access_code: '', ip_address: '',
    }));
  });

  it('offers RRF and SBC modes for Duet', async () => {
    const user = userEvent.setup();
    render(<AddPrinterModal onClose={vi.fn()} onAdd={vi.fn()} existingSerials={[]} />);
    await user.selectOptions(screen.getByLabelText('Connection type'), 'duet');
    expect(screen.getByLabelText('Duet connection mode')).toHaveValue('standalone');
    await user.selectOptions(screen.getByLabelText('Authentication'), 'password');
    expect(screen.getByLabelText('Password')).toBeRequired();
    expect(screen.getByRole('status')).toHaveTextContent('Configuration only');
  });

  it('never populates a saved credential and permits keeping it on edit', () => {
    render(<PrinterConnectionFields value={{ connection_type: 'klipper', api_url: 'http://pi.local', auth_mode: 'api_key' }}
      editing hasSecret onChange={vi.fn()} />);
    expect(screen.getByLabelText('Connection type')).toBeDisabled();
    expect(screen.getByLabelText('API key')).toHaveValue('');
    expect(screen.getByLabelText('API key')).not.toBeRequired();
    expect(screen.getByPlaceholderText('Leave blank to keep saved credential')).toBeInTheDocument();
  });

  it('clears irrelevant saved fields when creating another connection type', () => {
    const result = connectionPayload({ name: 'Duet', connection_type: 'duet', serial_number: 'OLD',
      ip_address: 'old.local', access_code: 'old-code', api_url: 'http://duet.local', auth_mode: 'none', connection_secret: 'old-key' });
    expect(result).toMatchObject({ serial_number: '', ip_address: '', access_code: '',
      connection_secret: null, duet_mode: 'standalone' });
  });
});

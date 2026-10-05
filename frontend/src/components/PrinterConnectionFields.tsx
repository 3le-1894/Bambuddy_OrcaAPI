import type { PrinterCreate } from '../api/client';

type ConnectionSettings = Pick<PrinterCreate, 'connection_type' | 'api_url' | 'auth_mode' | 'duet_mode' | 'connection_secret'>;

const inputClass = 'w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white';

export function PrinterConnectionFields({ value, onChange, editing = false, hasSecret = false }: {
  value: ConnectionSettings;
  onChange: (next: ConnectionSettings) => void;
  editing?: boolean;
  hasSecret?: boolean;
}) {
  const type = value.connection_type || 'bambu';
  const auth = value.auth_mode || 'none';
  return <>
    <div>
      <label htmlFor="connection_type" className="block text-sm text-bambu-gray mb-1">Connection type</label>
      <select id="connection_type" className={inputClass} value={type} disabled={editing}
        onChange={event => onChange({ connection_type: event.target.value as ConnectionSettings['connection_type'],
          api_url: '', auth_mode: 'none', duet_mode: null, connection_secret: '' })}>
        <option value="bambu">Bambu Lab</option>
        <option value="klipper">Klipper / Moonraker — monitoring</option>
        <option value="duet">Duet / RepRapFirmware — configuration only</option>
      </select>
    </div>
    {type !== 'bambu' && <>
      <p className="text-sm text-amber-400" role="status">{type === 'klipper'
        ? 'Live monitoring through Moonraker. Print controls are not yet available.'
        : 'Configuration only. Monitoring and print controls will become available when this adapter is added.'}</p>
      <div>
        <label htmlFor="printer_api_url" className="block text-sm text-bambu-gray mb-1">Server URL</label>
        <input id="printer_api_url" type="url" required className={inputClass} value={value.api_url || ''}
          placeholder={type === 'klipper' ? 'http://printer.local:7125' : 'http://duet.local'}
          onChange={event => onChange({ ...value, api_url: event.target.value })} />
      </div>
      <div>
        <label htmlFor="printer_auth_mode" className="block text-sm text-bambu-gray mb-1">Authentication</label>
        <select id="printer_auth_mode" className={inputClass} value={auth}
          onChange={event => onChange({ ...value, auth_mode: event.target.value as ConnectionSettings['auth_mode'], connection_secret: '' })}>
          <option value="none">None</option>
          <option value={type === 'klipper' ? 'api_key' : 'password'}>{type === 'klipper' ? 'API key' : 'Password'}</option>
        </select>
      </div>
      {auth !== 'none' && <div>
        <label htmlFor="printer_connection_secret" className="block text-sm text-bambu-gray mb-1">{auth === 'api_key' ? 'API key' : 'Password'}</label>
        <input id="printer_connection_secret" type="password" autoComplete="new-password" className={inputClass}
          required={!editing || !hasSecret} value={value.connection_secret || ''}
          placeholder={editing && hasSecret ? 'Leave blank to keep saved credential' : 'Enter credential'}
          onChange={event => onChange({ ...value, connection_secret: event.target.value })} />
        {editing && hasSecret && <p className="text-xs text-bambu-gray mt-1">A credential is saved. Select None to remove it.</p>}
      </div>}
      {type === 'duet' && <div>
        <label htmlFor="printer_duet_mode" className="block text-sm text-bambu-gray mb-1">Duet connection mode</label>
        <select id="printer_duet_mode" className={inputClass} value={value.duet_mode || 'standalone'}
          onChange={event => onChange({ ...value, duet_mode: event.target.value as ConnectionSettings['duet_mode'] })}>
          <option value="standalone">Standalone RRF</option><option value="sbc">SBC / Duet Software Framework</option>
        </select>
      </div>}
    </>}
  </>;
}

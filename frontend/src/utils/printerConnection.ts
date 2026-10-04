import type { PrinterCreate } from '../api/client';

export function connectionPayload(form: PrinterCreate): PrinterCreate {
  if (!form.connection_type || form.connection_type === 'bambu') {
    return { ...form, api_url: null, auth_mode: 'none', duet_mode: null, connection_secret: null };
  }
  return {
    ...form,
    serial_number: '', ip_address: '', access_code: '',
    api_url: form.api_url?.trim(),
    duet_mode: form.connection_type === 'duet' ? (form.duet_mode || 'standalone') : null,
    connection_secret: form.auth_mode === 'none' ? null : form.connection_secret,
  };
}

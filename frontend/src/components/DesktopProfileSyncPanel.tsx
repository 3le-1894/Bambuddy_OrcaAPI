import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { RefreshCw, Loader2, ChevronDown, ChevronUp, AlertCircle, Layers } from 'lucide-react';
import { api } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

export function DesktopProfileSyncPanel({ importPending = false }: { importPending?: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const { data: status, isPending, isFetching, isError, refetch } = useQuery({
    queryKey: ['desktopProfileSyncStatus'],
    queryFn: () => api.getDesktopProfileSyncStatus(),
    refetchInterval: 30000,
    retry: false,
  });
  const sync = useMutation({
    mutationFn: () => api.syncDesktopProfiles(),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['localPresets'] });
      queryClient.invalidateQueries({ queryKey: ['slicerPresets'] });
      queryClient.invalidateQueries({ queryKey: ['desktopProfileSyncStatus'] });
      showToast(t('profiles.localProfiles.desktopSync.result', '{{updated}} updated, {{added}} added, {{unchanged}} unchanged.', { updated: result.updated, added: result.added, unchanged: result.unchanged }), result.conflicts.length ? 'warning' : 'success');
      setDetailsOpen(false);
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });
  const result = sync.data ?? (status?.synced_at ? status : undefined);
  const connected = !isError && status?.sidecar?.status === 'connected';
  const connectionLabel = isPending
    ? t('profiles.localProfiles.desktopSync.checking', 'Checking connection…')
    : connected
      ? t('profiles.localProfiles.desktopSync.connected', 'Sidecar connected')
      : status?.sidecar?.status === 'not_configured' && !isError
        ? t('profiles.localProfiles.desktopSync.notConfigured', 'Sync not configured')
        : t('profiles.localProfiles.desktopSync.offline', 'Sidecar unavailable');
  const conflicts = result?.conflicts ?? [];
  const missing = result?.missing ?? [];
  const changes = result?.changes ?? [];

  return (
    <section aria-labelledby="desktop-profile-sync-title" className="rounded-xl border border-bambu-dark-tertiary bg-bambu-dark-secondary overflow-hidden">
      <div className="p-4 sm:p-5 space-y-4">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 space-y-2">
            <h2 id="desktop-profile-sync-title" className="text-base font-semibold text-white flex items-center gap-2">
              <Layers className="w-5 h-5 text-bambu-green" aria-hidden="true" />
              {t('profiles.localProfiles.desktopSync.title', 'Desktop Orca profiles')}
            </h2>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-bambu-gray">
              <span className="inline-flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${isPending ? 'bg-bambu-gray' : connected ? 'bg-bambu-green' : 'bg-amber-400'}`} aria-hidden="true" />
                {connectionLabel}
              </span>
              {connected && status?.sidecar.version && <span>Orca {status.sidecar.version}</span>}
              <Button variant="ghost" size="sm" disabled={isFetching || sync.isPending} onClick={() => void refetch()} aria-label={t('profiles.localProfiles.desktopSync.refresh', 'Refresh sidecar connection')}>
                <RefreshCw className={`w-4 h-4 ${isFetching ? 'animate-spin' : ''}`} aria-hidden="true" />
              </Button>
            </div>
            <p className="text-xs text-bambu-gray">
              {result?.synced_at
                ? t('profiles.localProfiles.desktopSync.last', 'Last sync: {{time}}', { time: new Date(result.synced_at).toLocaleString() })
                : t('profiles.localProfiles.desktopSync.never', 'No profiles synced yet')}
              {status?.profile_count != null && <> · {t('profiles.localProfiles.desktopSync.count', '{{count}} synced profiles', { count: status.profile_count })}</>}
            </p>
          </div>
          <Button onClick={() => sync.mutate()} disabled={!connected || sync.isPending || importPending} className="w-full sm:w-auto shrink-0">
            {sync.isPending ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="w-4 h-4" aria-hidden="true" />}
            {sync.isPending ? t('profiles.localProfiles.desktopSync.running', 'Syncing desktop profiles…') : t('profiles.localProfiles.desktopSync.syncNow', 'Sync now')}
          </Button>
        </div>
        <p className="text-sm text-bambu-gray">{t('profiles.localProfiles.desktopSync.saveHint', 'Save your changes in desktop Orca before syncing.')}</p>
        {!isPending && !connected && <p className="text-sm text-amber-300">{t('profiles.localProfiles.desktopSync.connectionHint', 'Check the Windows Orca API service, then refresh the connection.')}</p>}
        {sync.isError && <p role="alert" className="text-sm text-red-400">{sync.error.message}</p>}
        {result && <>
          <div className="grid grid-cols-3 gap-2" role="status" aria-live="polite" aria-label={t('profiles.localProfiles.desktopSync.result', '{{updated}} updated, {{added}} added, {{unchanged}} unchanged.', { updated: result.updated ?? 0, added: result.added ?? 0, unchanged: result.unchanged ?? 0 })}>
            {[
              { label: t('profiles.localProfiles.desktopSync.updated', 'Updated'), value: result.updated ?? 0 },
              { label: t('profiles.localProfiles.desktopSync.added', 'Added'), value: result.added ?? 0 },
              { label: t('profiles.localProfiles.desktopSync.unchanged', 'Unchanged'), value: result.unchanged ?? 0 },
            ].map(item => <div key={item.label} className="rounded-lg bg-bambu-dark p-3 text-center"><p className="text-xl font-semibold text-white tabular-nums">{item.value}</p><p className="text-xs text-bambu-gray mt-1">{item.label}</p></div>)}
          </div>
          {(conflicts.length > 0 || missing.length > 0) && <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 flex flex-col sm:flex-row gap-2 sm:items-center sm:justify-between">
            <p className="text-sm text-amber-300 flex items-center gap-2"><AlertCircle className="w-4 h-4 shrink-0" aria-hidden="true" />{t('profiles.localProfiles.desktopSync.kept', '{{conflicts}} conflicts, {{missing}} missing from desktop. Existing profiles kept.', { conflicts: conflicts.length, missing: missing.length })}</p>
            <Button variant="ghost" size="sm" onClick={() => setDetailsOpen(true)}>{t('profiles.localProfiles.desktopSync.review', 'Review')}</Button>
          </div>}
          <Button variant="ghost" size="sm" onClick={() => setDetailsOpen(open => !open)} aria-expanded={detailsOpen} aria-controls="desktop-sync-details">
            {detailsOpen ? <ChevronUp className="w-4 h-4" aria-hidden="true" /> : <ChevronDown className="w-4 h-4" aria-hidden="true" />}
            {t('profiles.localProfiles.desktopSync.viewChanges', 'View last changes')}
          </Button>
        </>}
      </div>
      {detailsOpen && result && <div id="desktop-sync-details" className="border-t border-bambu-dark-tertiary p-4 sm:p-5 space-y-4 max-h-96 overflow-y-auto">
        {conflicts.length > 0 && <ResultList title={t('profiles.localProfiles.desktopSync.conflictTitle', 'Conflicts — kept Bambuddy edits')} names={conflicts} />}
        {missing.length > 0 && <ResultList title={t('profiles.localProfiles.desktopSync.missingTitle', 'Missing from desktop — kept existing profiles')} names={missing} />}
        {(['updated', 'added', 'unchanged'] as const).map(action => {
          const names = changes.filter(p => p.action === action).map(p => p.name);
          return names.length > 0 ? <ResultList key={action} title={t(`profiles.localProfiles.desktopSync.${action}`, action === 'updated' ? 'Updated' : action === 'added' ? 'Added' : 'Unchanged')} names={names} /> : null;
        })}
        {changes.length === 0 && <p className="text-sm text-bambu-gray">{t('profiles.localProfiles.desktopSync.olderResult', 'Run a sync to record the profile names for this view.')}</p>}
      </div>}
    </section>
  );
}

function ResultList({ title, names }: { title: string; names: string[] }) {
  return <div><h3 className="text-sm font-medium text-white mb-2">{title} <span className="text-bambu-gray">({names.length})</span></h3><ul className="space-y-1 text-sm text-bambu-gray">{names.map(name => <li key={name} className="break-words">{name}</li>)}</ul></div>;
}

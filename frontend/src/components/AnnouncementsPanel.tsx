import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, Megaphone, X } from 'lucide-react';
import type { Announcement, AnnouncementLevel } from '../api/client';
import { announcementText, isAllowedAnnouncementLink } from '../hooks/useAnnouncements';
import { formatDateOnly } from '../utils/date';

const LEVEL_CHIP: Record<AnnouncementLevel, string> = {
  info: 'bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300',
  important: 'bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300',
  critical: 'bg-red-100 text-red-800 dark:bg-red-500/20 dark:text-red-300',
};

interface AnnouncementsPanelProps {
  open: boolean;
  onClose: () => void;
  announcements: Announcement[];
  markRead: (id: string) => void;
}

/**
 * Slide-over list of announcements from the Bambuddy maintainers.
 *
 * Opening it marks everything in it read -- the dot and the banner go -- but the
 * ones that were unread keep a "New" chip until it closes, so the reader can
 * still tell what they came for.
 */
export function AnnouncementsPanel({ open, onClose, announcements, markRead }: AnnouncementsPanelProps) {
  const { t, i18n } = useTranslation();
  const [newIds, setNewIds] = useState<Set<string>>(() => new Set());
  const closeRef = useRef<HTMLButtonElement>(null);
  // The list at the moment of opening; later refetches don't re-mark anything.
  const latest = useRef({ announcements, markRead });
  latest.current = { announcements, markRead };

  useEffect(() => {
    if (!open) return;
    const unreadIds = latest.current.announcements.filter((a) => !a.read).map((a) => a.id);
    setNewIds(new Set(unreadIds));
    unreadIds.forEach((id) => latest.current.markRead(id));
    closeRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 bg-black/60 z-[60]" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="announcements-title"
        className="fixed inset-y-0 right-0 z-[61] w-full max-w-md bg-bambu-dark-secondary border-l border-bambu-dark-tertiary flex flex-col shadow-2xl"
      >
        <header className="flex items-center gap-3 px-5 py-4 border-b border-bambu-dark-tertiary">
          <Megaphone className="w-5 h-5 text-bambu-green" />
          <h2 id="announcements-title" className="text-lg font-semibold text-white">
            {t('announcements.title')}
          </h2>
          <button
            ref={closeRef}
            onClick={onClose}
            className="ml-auto p-2 -mr-2 rounded-lg text-bambu-gray-light hover:text-white hover:bg-bambu-dark-tertiary transition-colors"
            aria-label={t('common.close')}
            title={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto">
          {announcements.length === 0 ? (
            <p className="p-6 text-center text-bambu-gray">{t('announcements.empty')}</p>
          ) : (
            <ul className="divide-y divide-bambu-dark-tertiary">
              {announcements.map((a) => {
                const text = announcementText(a, i18n.language);
                const link = isAllowedAnnouncementLink(a.link_url) ? a.link_url : null;
                return (
                  <li key={a.id} className="px-5 py-4 space-y-2">
                    <div className="flex items-center gap-2 flex-wrap text-xs">
                      <span className={`px-2 py-0.5 rounded-full font-semibold ${LEVEL_CHIP[a.level]}`}>
                        {t(`announcements.level.${a.level}`)}
                      </span>
                      {newIds.has(a.id) && (
                        <span className="px-2 py-0.5 rounded-full font-semibold bg-bambu-green/20 text-bambu-green">
                          {t('announcements.new')}
                        </span>
                      )}
                      {a.published_at && (
                        <span className="ml-auto text-bambu-gray">{formatDateOnly(a.published_at)}</span>
                      )}
                    </div>
                    <h3 className="text-white font-semibold text-balance">{text.title}</h3>
                    <p className="text-sm text-bambu-gray-light whitespace-pre-line break-words">{text.body}</p>
                    {link && (
                      <a
                        href={link}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-sm font-medium text-bambu-green hover:underline"
                      >
                        {text.link_label || t('announcements.readMore')}
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <footer className="px-5 py-3 border-t border-bambu-dark-tertiary text-xs text-bambu-gray">
          {t('announcements.source')}
        </footer>
      </aside>
    </>
  );
}

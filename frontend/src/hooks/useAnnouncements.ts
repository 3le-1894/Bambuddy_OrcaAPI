import { useCallback, useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { announcementsApi, type Announcement, type AnnouncementText } from '../api/client';
import { useAuth } from '../contexts/AuthContext';

// Hosts a message may link to. The backend already drops any other link; this
// is the second check, so a bad link never becomes a clickable one.
const LINK_HOSTS = ['github.com', 'bambuddy.cool'];

export function isAllowedAnnouncementLink(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return false;
    if (parsed.port && parsed.port !== '443') return false;
    const host = parsed.hostname.toLowerCase();
    return LINK_HOSTS.some((allowed) => host === allowed || host.endsWith(`.${allowed}`));
  } catch {
    return false;
  }
}

/** The message in the UI language when it was written in it, else English. */
export function announcementText(announcement: Announcement, language: string): AnnouncementText {
  const { texts } = announcement;
  if (texts[language]) return texts[language];
  const lower = language.toLowerCase();
  const sameCase = Object.keys(texts).find((key) => key.toLowerCase() === lower);
  if (sameCase) return texts[sameCase];
  // "pt" finds "pt-BR", "zh-HK" finds "zh-TW" or "zh-CN" -- closer than English.
  const base = lower.split('-')[0];
  const sameBase = Object.keys(texts).find((key) => key.toLowerCase().split('-')[0] === base);
  return sameBase ? texts[sameBase] : texts.en;
}

const SEVERITY: Record<Announcement['level'], number> = { critical: 2, important: 1, info: 0 };

/**
 * Announcements from the Bambuddy maintainers, for whoever may see them.
 *
 * The backend answers with an empty list for anyone who may not (and when the
 * install has them switched off), so an empty list is the one signal for
 * "show nothing" -- no separate permission check here.
 */
export function useAnnouncements() {
  const { authEnabled, user, loading } = useAuth();
  const queryClient = useQueryClient();

  const { data } = useQuery({
    queryKey: ['announcements'],
    queryFn: announcementsApi.list,
    enabled: !loading && (!authEnabled || !!user),
    staleTime: 5 * 60 * 1000,
    // A local read of what the backend fetched; the backend itself only goes
    // to GitHub every few hours.
    refetchInterval: 10 * 60 * 1000,
  });
  const announcements = useMemo(() => data ?? [], [data]);

  const markReadMutation = useMutation({
    mutationFn: announcementsApi.markRead,
    onMutate: (id: string) => {
      queryClient.setQueryData<Announcement[]>(['announcements'], (old) =>
        old?.map((a) => (a.id === id ? { ...a, read: true } : a))
      );
    },
    onError: () => queryClient.invalidateQueries({ queryKey: ['announcements'] }),
  });
  const { mutate } = markReadMutation;
  const markRead = useCallback((id: string) => mutate(id), [mutate]);

  const unread = useMemo(() => announcements.filter((a) => !a.read), [announcements]);

  // What earns a banner: important or critical, not yet read. Most severe first,
  // then newest (the list already comes newest first).
  const bannerItems = useMemo(
    () =>
      unread
        .filter((a) => a.level !== 'info')
        .sort((a, b) => SEVERITY[b.level] - SEVERITY[a.level]),
    [unread]
  );

  return { announcements, unread, bannerItems, markRead };
}

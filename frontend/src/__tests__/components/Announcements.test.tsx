/**
 * Announcements from the Bambuddy maintainers: the sidebar entry above System,
 * the slide-over list, and the banner for unread important/critical messages.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { AnnouncementsPanel } from '../../components/AnnouncementsPanel';
import { AnnouncementBanner } from '../../components/AnnouncementBanner';
import { Layout } from '../../components/Layout';
import type { Announcement } from '../../api/client';

function announcement(overrides: Partial<Announcement> = {}): Announcement {
  return {
    id: 'a1',
    level: 'info',
    texts: { en: { title: 'Hello installs', body: 'Line one\nLine two' } },
    link_url: null,
    published_at: '2026-10-01T12:00:00Z',
    expires_at: null,
    read: false,
    ...overrides,
  };
}

describe('AnnouncementsPanel', () => {
  it('marks what was unread as read on opening, and keeps a New chip on it', () => {
    const markRead = vi.fn();
    render(
      <AnnouncementsPanel
        open
        onClose={() => {}}
        markRead={markRead}
        announcements={[announcement(), announcement({ id: 'a2', read: true, texts: { en: { title: 'Old news', body: 'x' } } })]}
      />
    );
    expect(markRead).toHaveBeenCalledTimes(1);
    expect(markRead).toHaveBeenCalledWith('a1');
    const items = screen.getAllByRole('listitem');
    expect(within(items[0]).getByText('New')).toBeInTheDocument();
    expect(within(items[1]).queryByText('New')).not.toBeInTheDocument();
  });

  it('renders the body as plain text, never as HTML', () => {
    render(
      <AnnouncementsPanel
        open
        onClose={() => {}}
        markRead={() => {}}
        announcements={[announcement({ texts: { en: { title: 'T', body: '<img src=x onerror=alert(1)>' } } })]}
      />
    );
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(document.querySelector('img[src="x"]')).toBeNull();
  });

  it('links only to allowed hosts', () => {
    render(
      <AnnouncementsPanel
        open
        onClose={() => {}}
        markRead={() => {}}
        announcements={[
          announcement({ id: 'ok', link_url: 'https://wiki.bambuddy.cool/x/', texts: { en: { title: 'A', body: 'b', link_label: 'Details' } } }),
          announcement({ id: 'bad', link_url: 'https://evil.example/x', texts: { en: { title: 'B', body: 'b', link_label: 'Phish' } } }),
        ]}
      />
    );
    const link = screen.getByRole('link', { name: /Details/ });
    expect(link).toHaveAttribute('href', 'https://wiki.bambuddy.cool/x/');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.queryByRole('link', { name: /Phish/ })).not.toBeInTheDocument();
  });

  it('closes on Escape', () => {
    const onClose = vi.fn();
    render(<AnnouncementsPanel open onClose={onClose} markRead={() => {}} announcements={[announcement()]} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});

describe('AnnouncementBanner', () => {
  it('shows the first item, offers the rest, and Got it marks it read', () => {
    const markRead = vi.fn();
    const onOpen = vi.fn();
    render(
      <AnnouncementBanner
        items={[
          announcement({ id: 'crit', level: 'critical', texts: { en: { title: 'Update now', body: 'b' } } }),
          announcement({ id: 'imp', level: 'important' }),
        ]}
        onOpen={onOpen}
        markRead={markRead}
      />
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Update now');
    fireEvent.click(screen.getByRole('button', { name: 'Read more (+1)' }));
    expect(onOpen).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    expect(markRead).toHaveBeenCalledWith('crit');
  });

  it('renders nothing without items', () => {
    render(<AnnouncementBanner items={[]} onOpen={() => {}} markRead={() => {}} />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});

describe('Layout with announcements', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
      http.get('/api/v1/settings/ui-flags', () =>
        HttpResponse.json({ check_updates: false, billing_enabled: false, user_notifications_enabled: true, currency: 'EUR' })
      )
    );
  });

  it('shows no entry and no banner when there is nothing', async () => {
    render(<Layout />);
    await waitFor(() => expect(screen.getAllByTitle(/System/).length).toBeGreaterThan(0));
    expect(screen.queryByText('Announcements')).not.toBeInTheDocument();
  });

  it('shows the entry with an unread count, the banner for important, and opens the list', async () => {
    let reads: string[] = [];
    server.use(
      http.get('/api/v1/announcements', () =>
        HttpResponse.json([
          announcement({ id: 'imp', level: 'important', texts: { en: { title: 'Breaking change in 2.0', body: 'b' } } }),
          announcement({ id: 'inf', level: 'info', texts: { en: { title: 'Testers wanted', body: 'b' } } }),
        ])
      ),
      http.post('/api/v1/announcements/:id/read', ({ params }) => {
        reads = [...reads, String(params.id)];
        return new HttpResponse(null, { status: 204 });
      })
    );
    render(<Layout />);

    const entry = await screen.findByRole('button', { name: /Announcements/ });
    expect(within(entry).getByText('2')).toBeInTheDocument();
    // Only the important one earns a banner.
    expect(screen.getByRole('status')).toHaveTextContent('Breaking change in 2.0');
    expect(screen.queryByText('Testers wanted')).not.toBeInTheDocument();

    fireEvent.click(entry);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Testers wanted')).toBeInTheDocument();
    await waitFor(() => expect(reads.sort()).toEqual(['imp', 'inf']));
    // Read now: the banner is gone.
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
  });
});

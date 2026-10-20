/**
 * Tests for LocalProfilesView component.
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, fireEvent, render as rawRender } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { server } from '../mocks/server';
import { render } from '../utils';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { AuthProvider } from '../../contexts/AuthContext';
import { LocalProfilesView } from '../../components/LocalProfilesView';

const mockLocalPresets = {
  filament: [
    {
      id: 1,
      name: 'Overture PLA Matte @BBL X1C',
      preset_type: 'filament',
      source: 'orcaslicer',
      filament_type: 'PLA',
      filament_vendor: 'Overture',
      nozzle_temp_min: 190,
      nozzle_temp_max: 230,
      pressure_advance: '["0.04"]',
      default_filament_colour: '["#FFAA00"]',
      filament_cost: '24.99',
      filament_density: '1.24',
      compatible_printers: '["Bambu Lab X1 Carbon 0.4 nozzle"]',
      inherits: 'Bambu PLA Basic @BBL X1C',
      version: '2.3.0.4',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 2,
      name: 'eSUN PETG @Bambu Lab H2D',
      preset_type: 'filament',
      source: 'orcaslicer',
      filament_type: 'PETG',
      filament_vendor: null,
      nozzle_temp_min: 220,
      nozzle_temp_max: 250,
      pressure_advance: null,
      default_filament_colour: null,
      filament_cost: null,
      filament_density: null,
      compatible_printers: null,
      inherits: null,
      version: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
  ],
  process: [
    {
      id: 3,
      name: '0.20mm Standard @BBL X1C',
      preset_type: 'process',
      source: 'orcaslicer',
      filament_type: null,
      filament_vendor: null,
      nozzle_temp_min: null,
      nozzle_temp_max: null,
      pressure_advance: null,
      default_filament_colour: null,
      filament_cost: null,
      filament_density: null,
      compatible_printers: null,
      inherits: '0.20mm Standard @BBL X1C',
      version: '2.3.0.4',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
  ],
  printer: [],
};

describe('LocalProfilesView', () => {
  it('refreshes a stale missing warning after an empty cleanup preview', async () => {
    const result = { added: 0, updated: 0, unchanged: 16, conflicts: [], missing: ['Already removed'], synced_at: '2026-10-07T20:00:00Z' };
    let missing = ['Already removed'];
    server.use(
      http.get('/api/v1/local-presets/desktop-sync/status', () => HttpResponse.json({ ...result, missing, profile_count: 16, sidecar: { status: 'connected', version: '2.5.0-dev' } })),
      http.post('/api/v1/local-presets/desktop-sync', () => HttpResponse.json(result)),
      http.post('/api/v1/local-presets/desktop-sync/cleanup', () => {
        missing = [];
        return HttpResponse.json({ profiles: [], removed: [] });
      }),
    );
    render(<LocalProfilesView />);
    const syncButton = await screen.findByRole('button', { name: 'Sync now' });
    await waitFor(() => expect(syncButton).toBeEnabled());
    fireEvent.click(syncButton);
    await screen.findByText('0 conflicts, 1 missing from desktop. Existing profiles kept.');
    const cleanupButton = screen.getByRole('button', { name: 'Remove profiles deleted locally' });
    await waitFor(() => expect(cleanupButton).toBeEnabled());
    fireEvent.click(cleanupButton);
    await screen.findByText('No deleted desktop profiles found.');
    await waitFor(() => expect(screen.queryByText('0 conflicts, 1 missing from desktop. Existing profiles kept.')).not.toBeInTheDocument());
  });

  it('previews deleted copies and removes only an explicitly selected eligible profile', async () => {
    const requests: unknown[] = [];
    server.use(
      http.get('/api/v1/local-presets/desktop-sync/status', () => HttpResponse.json({ synced_at: null, profile_count: 2, sidecar: { status: 'connected', version: '2.5.0-dev' } })),
      http.post('/api/v1/local-presets/desktop-sync/cleanup', async ({ request }) => {
        const body = await request.json() as { ids?: number[] };
        requests.push(body);
        return HttpResponse.json({ profiles: [
          { id: 51, name: 'Deleted PLA', preset_type: 'filament', reasons: [] },
          { id: 52, name: 'Edited PETG', preset_type: 'filament', reasons: ['Edited in farm manager'] },
        ], removed: body.ids ? ['Deleted PLA'] : [] });
      }),
    );
    render(<LocalProfilesView />);
    const preview = await screen.findByRole('button', { name: 'Remove profiles deleted locally' });
    await waitFor(() => expect(preview).toBeEnabled());
    fireEvent.click(preview);
    const eligible = await screen.findByRole('checkbox', { name: /Deleted PLA/ });
    expect(screen.getByRole('checkbox', { name: /Edited PETG/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Remove selected copies (0)' })).toBeDisabled();
    expect(requests).toEqual([{}]);
    fireEvent.click(eligible);
    fireEvent.click(screen.getByRole('button', { name: 'Remove selected copies (1)' }));
    await waitFor(() => expect(requests).toEqual([{}, { ids: [51] }]));
    await waitFor(() => expect(screen.queryByRole('checkbox', { name: /Deleted PLA/ })).not.toBeInTheDocument());
  });

  beforeEach(() => {
    vi.mocked(localStorage.getItem).mockReturnValue(null);
    server.use(
      http.get('/api/v1/local-presets/desktop-sync/status', () => HttpResponse.json({ synced_at: null, profile_count: 0, sidecar: { status: 'connected', version: '2.5.0-dev' } })),
      http.get('/api/v1/local-presets/', () => {
        return HttpResponse.json(mockLocalPresets);
      }),
      http.delete('/api/v1/local-presets/:id', () => {
        return HttpResponse.json({ success: true });
      }),
    );
  });

  it('groups by compatibility metadata and repeats shared profiles without duplicate membership', async () => {
    server.use(http.get('/api/v1/local-presets/', () => HttpResponse.json({
      ...mockLocalPresets,
      filament: [{ ...mockLocalPresets.filament[0], compatible_printers: '["Vulcan", "Heaphtus", "Vulcan"]' },
        { ...mockLocalPresets.filament[1], compatible_printers: '{invalid}' }],
    })));
    render(<LocalProfilesView />);
    await screen.findByText('Overture PLA Matte @BBL X1C');
    fireEvent.change(screen.getByRole('combobox', { name: 'Group by' }), { target: { value: 'printer' } });
    expect(screen.getAllByText('Overture PLA Matte @BBL X1C')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Vulcan (1)' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: 'Heaphtus (1)' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Unspecified (1)' })).toHaveLength(2);
    expect(screen.queryByRole('button', { name: /H2D/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Collapse all' }));
    expect(screen.queryByText('Overture PLA Matte @BBL X1C')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Expand all' }));
    expect(screen.getAllByText('Overture PLA Matte @BBL X1C')).toHaveLength(2);
    fireEvent.change(screen.getByRole('combobox', { name: 'Group by' }), { target: { value: 'none' } });
    expect(screen.getAllByText('Overture PLA Matte @BBL X1C')).toHaveLength(1);
  });

  it('groups material and brand, remembers the selection and reveals search matches', async () => {
    render(<LocalProfilesView />);
    await screen.findByText('Overture PLA Matte @BBL X1C');
    const grouping = screen.getByRole('combobox', { name: 'Group by' });
    fireEvent.change(grouping, { target: { value: 'material' } });
    expect(screen.getByRole('button', { name: 'PLA (1)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'PETG (1)' })).toBeInTheDocument();
    fireEvent.change(grouping, { target: { value: 'brand' } });
    expect(screen.getByRole('button', { name: 'Overture (1)' })).toBeInTheDocument();
    expect(localStorage.setItem).toHaveBeenCalledWith('localProfiles.groupBy', 'brand');
    fireEvent.click(screen.getByRole('button', { name: 'Overture (1)' }));
    expect(screen.queryByText('Overture PLA Matte @BBL X1C')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Overture' } });
    expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Collapse all' })).toBeDisabled();
  });

  it('restores a saved grouping choice', async () => {
    vi.mocked(localStorage.getItem).mockImplementation(key => key === 'localProfiles.groupBy' ? 'material' : null);
    render(<LocalProfilesView />);
    expect(await screen.findByRole('combobox', { name: 'Group by' })).toHaveValue('material');
    expect(screen.getByRole('button', { name: 'PLA (1)' })).toBeInTheDocument();
  });

  it('renders filament and process columns', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    expect(screen.getByText('eSUN PETG @Bambu Lab H2D')).toBeInTheDocument();
    expect(screen.getByText('0.20mm Standard @BBL X1C')).toBeInTheDocument();
  });

  it('syncs desktop profiles and shows conflicts without hiding the results', async () => {
    server.use(http.post('/api/v1/local-presets/desktop-sync', () => HttpResponse.json({
      added: 1, updated: 2, unchanged: 13, conflicts: ['Edited PLA'], missing: ['Old PETG'], synced_at: '2026-10-04T05:00:00Z',
    })));
    render(<LocalProfilesView />);
    const syncButton = await screen.findByRole('button', { name: 'Sync now' });
    await waitFor(() => expect(syncButton).toBeEnabled());
    fireEvent.click(syncButton);
    expect(await screen.findByRole('status')).toHaveAttribute('aria-label', '2 updated, 1 added, 13 unchanged.');
    fireEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect(screen.getByText('Edited PLA')).toBeInTheDocument();
    expect(screen.getByText('Old PETG')).toBeInTheDocument();
  });

  it('shows material badges from filament_type', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    // PLA badge should appear for the first preset
    const plaBadges = screen.getAllByText('PLA');
    expect(plaBadges.length).toBeGreaterThan(0);
  });

  it('loads saved sync results and profile names after reopening the page', async () => {
    server.use(http.get('/api/v1/local-presets/desktop-sync/status', () => HttpResponse.json({
      synced_at: '2026-10-04T05:00:00Z', profile_count: 16,
      sidecar: { status: 'connected', version: '2.5.0-dev' },
      updated: 1, added: 0, unchanged: 15, conflicts: [], missing: [],
      changes: [{ name: 'Saved desktop PLA', preset_type: 'filament', action: 'updated' }],
    })));
    render(<LocalProfilesView />);
    expect(await screen.findByText('Sidecar connected')).toBeInTheDocument();
    expect(screen.getByText('Orca 2.5.0-dev')).toBeInTheDocument();
    expect(screen.getByText(/16 synced profiles/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'View last changes' }));
    expect(screen.getByText('Saved desktop PLA')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'View last changes' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('disables sync while offline and lets the user refresh the connection', async () => {
    server.use(http.get('/api/v1/local-presets/desktop-sync/status', () => HttpResponse.json({
      synced_at: null, profile_count: 0, sidecar: { status: 'unreachable', version: null },
    })));
    render(<LocalProfilesView />);
    expect(await screen.findByText('Sidecar unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sync now' })).toBeDisabled();
    server.use(http.get('/api/v1/local-presets/desktop-sync/status', () => HttpResponse.json({
      synced_at: null, profile_count: 0, sidecar: { status: 'connected', version: '2.5.0-dev' },
    })));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh sidecar connection' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sync now' })).toBeEnabled());
  });

  it('shows vendor from filament_vendor field', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture')).toBeInTheDocument();
    });
  });

  it('parses vendor from name when filament_vendor is null', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('eSUN PETG @Bambu Lab H2D')).toBeInTheDocument();
    });

    // eSUN should be parsed from the name
    expect(screen.getByText('eSUN')).toBeInTheDocument();
  });

  it('filters presets by search query', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: 'PETG' } });

    expect(screen.queryByText('Overture PLA Matte @BBL X1C')).not.toBeInTheDocument();
    expect(screen.getByText('eSUN PETG @Bambu Lab H2D')).toBeInTheDocument();
  });

  it('keeps the search bar visible when no presets match the query (#1470)', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: 'zzz-nothing-matches' } });

    // The search bar must stay mounted so the query can be cleared/edited
    // without a page refresh, and it keeps the typed value.
    expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/search/i)).toHaveValue('zzz-nothing-matches');
    // A no-matches message replaces the columns (not the "import some" empty state).
    expect(screen.getByText(/no presets match your search/i)).toBeInTheDocument();
  });

  it('shows empty state when no presets', async () => {
    server.use(
      http.get('/api/v1/local-presets/', () => {
        return HttpResponse.json({ filament: [], process: [], printer: [] });
      }),
    );

    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText(/no local presets/i)).toBeInTheDocument();
    });
  });

  it('shows Local badge on preset cards', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    const badges = screen.getAllByText(/^Local$/i);
    expect(badges.length).toBeGreaterThan(0);
  });

  it('shows delete confirmation modal', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    // Click first delete button
    const deleteButtons = screen.getAllByTitle(/delete/i);
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(screen.getByText(/are you sure/i)).toBeInTheDocument();
    });
  });

  it('shows import zone', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText(/import profiles/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/\.bbscfg/i)).toBeInTheDocument();
  });

  it('invalidates the slicerPresets query after a delete (#1581)', async () => {
    // Without this invalidation a preset deleted in Local Profiles still
    // shows in the SliceModal until the modal's ['slicerPresets'] query
    // staleTime (60s) expires + a refocus / remount. The bug report:
    // "Removed local profiles still show on the slice menu even tho they
    // have been deleted." We mirror the production provider tree but inject
    // our own QueryClient so we can spy on invalidateQueries.
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    rawRender(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <ThemeProvider>
              <ToastProvider>
                <LocalProfilesView />
              </ToastProvider>
            </ThemeProvider>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    // Open the delete confirmation, then confirm. The card-level delete
    // icon buttons and the modal's Delete button both expose the accessible
    // name "Delete"; the modal button is the last one to mount, so it's the
    // tail of the findAllByRole result.
    const deleteButtons = screen.getAllByTitle(/delete/i);
    fireEvent.click(deleteButtons[0]);
    await screen.findByText(/are you sure/i);
    const confirmButtons = await screen.findAllByRole('button', { name: /^delete$/i });
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => {
      expect(
        invalidateSpy.mock.calls.some(
          ([arg]) => arg && (arg as { queryKey: unknown[] }).queryKey?.[0] === 'slicerPresets',
        ),
      ).toBe(true);
    });
    // Sanity: the local-only invalidation is still there too.
    expect(
      invalidateSpy.mock.calls.some(
        ([arg]) => arg && (arg as { queryKey: unknown[] }).queryKey?.[0] === 'localPresets',
      ),
    ).toBe(true);
  });
});

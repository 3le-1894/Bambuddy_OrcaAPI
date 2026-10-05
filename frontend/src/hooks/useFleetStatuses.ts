import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

// One shared dashboard request regardless of the number of Klipper cards.
export function useFleetStatuses(enabled = true, poll = false) {
  return useQuery({
    queryKey: ['fleetStatuses'], queryFn: api.getFleetStatuses,
    enabled, refetchInterval: poll ? 5000 : false, staleTime: 3000, retry: false,
    refetchOnMount: poll,
  });
}

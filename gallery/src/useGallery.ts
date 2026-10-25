import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Listing, ListingPage } from './model';
import { request } from './api';

function useRevision() {
  return useQuery({ queryKey: ['gallery', 'revision'], queryFn: ({ signal }) =>
    request<{ revision: number }>('/api/revision', undefined, signal), refetchInterval: 15000, refetchOnWindowFocus: true, retry: false });
}

export function useListing(id: string | undefined, pages: { notes_page: number; correspondence_page: number; viewings_page: number }) {
  const revision = useRevision();
  const parameters = new URLSearchParams(Object.entries(pages).map(([key, value]) => [key, String(value)]));
  return useQuery({ queryKey: ['gallery', 'detail', id, pages, revision.data?.revision],
    queryFn: ({ signal }) => request<Listing>(`/api/listings/${encodeURIComponent(id!)}?${parameters}`, undefined, signal), enabled: !!id, retry: false });
}

export function useGallery(view: string, page: number) {
  const client = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const revision = useRevision();
  const query = useQuery({ queryKey: ['gallery', 'list', view, page, revision.data?.revision],
    queryFn: ({ signal }) => request<ListingPage>(`/api/listings?view=${view}&page=${page}`, undefined, signal),
    enabled: view !== 'Notes' && revision.isSuccess, retry: false });
  const mutation = useMutation({
    mutationFn: ({ path, body }: { path: string; body: Record<string, unknown> }) => request(path, body),
    onMutate: () => client.cancelQueries({ queryKey: ['gallery'] }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['gallery'] }),
  });
  return {
    state: query.data,
    readError: query.error?.message ?? revision.error?.message ?? '',
    saveError: mutation.error?.message ?? '',
    busy: mutation.isPending, loading: refreshing,
    refresh: async () => { mutation.reset(); setRefreshing(true);
      try { await client.invalidateQueries({ queryKey: ['gallery'] }); } finally { setRefreshing(false); } },
    save: async (path: string, body: Record<string, unknown>) => {
      try { await mutation.mutateAsync({ path, body }); return true; }
      catch { return false; }
    },
  };
}

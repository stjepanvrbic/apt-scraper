import { useEffect, useRef, useState } from 'react';
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Comparison, Listing, ListingPage } from './model';
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
  const comparing = useIsMutating({ mutationKey: ['gallery', 'compare'] }) > 0;
  const [refreshing, setRefreshing] = useState(false);
  const revision = useRevision();
  const query = useQuery({ queryKey: ['gallery', 'list', view, page, revision.data?.revision],
    queryFn: ({ signal }) => request<ListingPage>(`/api/listings?view=${view}&page=${page}`, undefined, signal),
    enabled: view !== 'Notes' && view !== 'Compare' && revision.isSuccess, retry: false });
  const mutation = useMutation({
    mutationKey: ['gallery', 'listing-action'],
    mutationFn: ({ path, body }: { path: string; body: Record<string, unknown> }) => request(path, body),
    onMutate: () => client.cancelQueries({ queryKey: ['gallery'] }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['gallery'] }),
  });
  return {
    state: query.data,
    readError: query.error?.message ?? revision.error?.message ?? '',
    saveError: mutation.error?.message ?? '',
    busy: mutation.isPending || comparing, loading: refreshing,
    refresh: async () => { mutation.reset(); setRefreshing(true);
      try { await client.invalidateQueries({ queryKey: ['gallery'] }); } finally { setRefreshing(false); } },
    save: async (path: string, body: Record<string, unknown>) => {
      try { await mutation.mutateAsync({ path, body }); return true; }
      catch { return false; }
    },
  };
}

type ComparisonAction = { action: 'answer'; chosen: string; other: string } | { action: 'skip'; first: string; second: string };
const compareKey = ['gallery', 'compare'];

export function useCompare() {
  const client = useQueryClient();
  const revision = useRevision().data?.revision;
  const observedRevision = useRef<number | undefined>(undefined);
  const saving = useRef(false);
  const [announcement, setAnnouncement] = useState('');
  const pending = useIsMutating({ mutationKey: compareKey }) > 0;
  const updating = useIsMutating({ mutationKey: ['gallery'] }) > 0;
  const query = useQuery({ queryKey: compareKey,
    queryFn: ({ signal }) => request<Comparison>('/api/compare', undefined, signal), retry: false,
  });
  useEffect(() => {
    if (revision === undefined || updating) return;
    const previous = observedRevision.current;
    observedRevision.current = revision;
    if (previous !== undefined && previous !== revision) void client.invalidateQueries({ queryKey: compareKey });
  }, [client, revision, updating]);
  const mutation = useMutation({ mutationKey: compareKey,
    mutationFn: ({ action, ...body }: ComparisonAction) => request(`/api/compare/${action}`, body),
    onMutate: async () => { setAnnouncement(''); await client.cancelQueries({ queryKey: compareKey }); },
    onSuccess: async (_, variables) => {
      setAnnouncement(variables.action === 'answer' ? 'Choice saved.' : 'Pair skipped.');
      client.setQueryData<Comparison>(compareKey, previous => previous && ({ question: null,
        answered: previous.answered + (variables.action === 'answer' ? 1 : 0) }));
      await client.invalidateQueries({ queryKey: compareKey });
    },
  });
  const { isError, variables, reset } = mutation;
  useEffect(() => {
    if (!isError || !variables || !query.isSuccess) return;
    const identifiers = variables.action === 'answer' ? [variables.chosen, variables.other] : [variables.first, variables.second];
    if (!query.data.question?.listings.every(listing => identifiers.includes(listing.id))) reset();
  }, [isError, variables, reset, query.isSuccess, query.data]);
  const save = async (action: ComparisonAction) => {
    if (saving.current || updating || query.isFetching || query.isError) return;
    saving.current = true;
    try { await mutation.mutateAsync(action); }
    catch { return; }
    finally { saving.current = false; }
  };
  return { query, mutation, pending, busy: updating || query.isFetching, announcement, save };
}

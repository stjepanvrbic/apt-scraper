import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';
import type { RerankState, RerankSummary } from './model';

const rerankKey = ['rerank'];

export function useRerank() {
  const client = useQueryClient();
  const saving = useRef(false);
  const query = useQuery({ queryKey: rerankKey,
    queryFn: ({ signal }) => request<RerankState>('/api/rerank', undefined, signal),
    retry: false, refetchOnWindowFocus: true,
    refetchInterval: query => {
      const state = query.state.data;
      return query.state.error || state?.running || ['waiting', 'running'].includes(state?.last_rerank?.review?.state ?? '') ? 1000 : 15000;
    },
  });
  const summary = query.data?.last_rerank;
  const rerankId = summary?.id;
  const finishedReview = summary?.review?.state === 'done' ? summary.review.updated_at : undefined;
  useEffect(() => {
    if (rerankId === undefined) return;
    void client.invalidateQueries({ queryKey: ['gallery', 'list'] });
    void client.invalidateQueries({ queryKey: ['gallery', 'detail'] });
  }, [client, rerankId, finishedReview]);
  const mutation = useMutation({
    mutationKey: rerankKey,
    mutationFn: () => request<RerankSummary>('/api/rerank', {}),
    onSuccess: async summary => {
      await client.cancelQueries({ queryKey: rerankKey });
      client.setQueryData<RerankState>(rerankKey, previous => ({ running: false, last_rerank: summary, hypotheses: previous?.hypotheses ?? [] }));
    },
    onError: () => { void client.invalidateQueries({ queryKey: rerankKey }); },
  });
  const run = async () => {
    if (saving.current || query.data?.running || !query.isSuccess) return;
    saving.current = true;
    try { await mutation.mutateAsync(); }
    catch { return; }
    finally { saving.current = false; }
  };
  const hypothesis = useMutation({
    mutationKey: ['gallery', 'hypothesis'],
    mutationFn: ({ id, decision }: { id: string; decision: 'confirm' | 'reject' }) => request(`/api/hypotheses/${encodeURIComponent(id)}/${decision}`, {}),
    onSuccess: async () => {
      await Promise.all([client.invalidateQueries({ queryKey: rerankKey }), client.invalidateQueries({ queryKey: ['gallery'] })]);
    },
  });
  return { query, mutation, hypothesis, run, running: mutation.isPending || !!query.data?.running };
}

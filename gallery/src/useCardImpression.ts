import { useEffect, useRef } from 'react';
import { request } from './api';
import type { ListingSummary } from './model';

const visit = crypto.randomUUID();

export function useCardImpression(listing: ListingSummary, enabled: boolean) {
  const element = useRef<HTMLElement>(null);
  const { id, content_fingerprint: fingerprint, rank } = listing;
  useEffect(() => {
    if (!enabled || !element.current) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let visible = false;
    let recorded = false;
    let active = true;
    const eventId = crypto.randomUUID();
    let occurredAt: string | undefined;
    const reconsider = () => {
      clearTimeout(timer);
      if (recorded || !visible || document.visibilityState !== 'visible') return;
      timer = setTimeout(async () => {
        recorded = true;
        occurredAt ??= new Date().toISOString();
        try {
          await request('/api/signals', { events: [{ event_id: eventId, visit_id: visit, listing_id: id,
            fingerprint, kind: 'impression', rank, occurred_at: occurredAt }] });
        } catch (error) {
          console.warn('Card impression was not saved; it will be retried while visible.', error);
          recorded = false;
          if (active) timer = setTimeout(reconsider, 5000);
        }
      }, 1000);
    };
    const observer = new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting && entries[0].intersectionRatio >= 0.5;
      reconsider();
    }, { threshold: 0.5 });
    observer.observe(element.current);
    document.addEventListener('visibilitychange', reconsider);
    return () => { active = false; clearTimeout(timer); observer.disconnect(); document.removeEventListener('visibilitychange', reconsider); };
  }, [id, fingerprint, rank, enabled]);
  return element;
}

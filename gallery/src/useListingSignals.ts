import { useEffect, useState } from 'react';
import { useInterval, useWindowEvent } from '@mantine/hooks';
import { request } from './api';

type Action = { kind: 'open' | 'close' } | { kind: 'dwell'; duration_ms: number } | { kind: 'photo'; photo_url: string };
type Visit = { visit_id: string; listing_id: string; fingerprint: string };
type Signal = Visit & Action & { event_id: string; occurred_at: string };

function recorder() {
  const queue: Signal[] = [];
  let sending = false;
  let visit: Visit | undefined;
  let complete = true;
  let since: number | undefined;
  let selected: string | undefined;
  let fingerprint: string | undefined;
  let photo: string | undefined;
  let visible = document.visibilityState === 'visible';

  async function flush() {
    if (sending || !queue.length) return;
    sending = true;
    try {
      while (queue.length) {
        const batch = queue.slice(0, 20);
        while (new TextEncoder().encode(JSON.stringify({ events: batch })).length > 60000) batch.pop();
        await request('/api/signals', { events: batch }, undefined, { keepalive: true });
        queue.splice(0, batch.length);
      }
    } catch (error) {
      console.warn('Gallery signals could not be saved; the batch will be retried.', error);
    } finally {
      sending = false;
    }
  }

  function record(action: Action) {
    if (!visit) return;
    if (queue.length >= 200) {
      complete = false;
      console.warn('Gallery signal buffer is full; this event was not recorded.');
      return;
    }
    const event = { ...visit, ...action, event_id: crypto.randomUUID(), occurred_at: new Date().toISOString() };
    if (new TextEncoder().encode(JSON.stringify({ events: [event] })).length > 60000) {
      complete = false;
      console.warn('Gallery signal exceeds the request limit; this event was not recorded.');
      return;
    }
    queue.push(event);
    if (queue.length >= 20) void flush();
  }

  function checkpoint() {
    if (since === undefined) return;
    const now = performance.now();
    record({ kind: 'dwell', duration_ms: Math.min(86400000, Math.max(0, Math.round(now - since))) });
    since = now;
  }

  function resume() {
    if (!visible || !selected || !fingerprint) return;
    if (!visit) {
      visit = { visit_id: crypto.randomUUID(), listing_id: selected, fingerprint };
      complete = true;
      record({ kind: 'open' });
    }
    since ??= performance.now();
  }

  function finish() {
    checkpoint();
    if (complete) record({ kind: 'close' });
    visit = undefined;
    since = undefined;
    photo = undefined;
    void flush();
  }

  return {
    select(id: string | undefined, content: string | undefined) {
      if (selected !== id || (content && visit && visit.fingerprint !== content)) finish();
      selected = id;
      fingerprint = content;
      if (!content) { checkpoint(); since = undefined; }
      else resume();
    },
    photo(url: string | undefined) {
      if (url && url !== photo && visible) record({ kind: 'photo', photo_url: url });
      photo = url;
    },
    visibility() {
      checkpoint();
      since = undefined;
      visible = document.visibilityState === 'visible';
      resume();
      if (!visible) void flush();
    },
    pagehide() { finish(); },
    pageshow() { visible = document.visibilityState === 'visible'; resume(); },
    tick() { checkpoint(); void flush(); },
    finish,
  };
}

export function useListingSignals(id: string | undefined, fingerprint: string | undefined) {
  const [capture] = useState(recorder);
  useEffect(() => { capture.select(id, fingerprint); }, [capture, id, fingerprint]);
  useEffect(() => () => capture.finish(), [capture]);
  useWindowEvent('visibilitychange', capture.visibility, true);
  useWindowEvent('pagehide', capture.pagehide);
  useWindowEvent('pageshow', capture.pageshow);
  useInterval(capture.tick, 10000, { autoInvoke: true });
  return capture.photo;
}

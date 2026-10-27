import { useState } from 'react';
import { Button, Group, Stack, Text, TextInput } from '@mantine/core';
import type { Save } from './ListingCard';

export function FeedbackReason({ id, kind, save, close }: { id: string; kind: 'favorite' | 'dismiss'; save: Save; close: () => void }) {
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  return <form onSubmit={async event => {
    event.preventDefault();
    if (!text.trim() || saving) return;
    setSaving(true);
    setFailed(false);
    if (await save(`/api/listings/${encodeURIComponent(id)}/notes`, { text: text.trim() })) close();
    else setFailed(true);
    setSaving(false);
  }}>
    <Stack gap="xs">
      <TextInput label={kind === 'favorite' ? 'Why did you favorite this place? (optional)' : 'What ruled this place out? (optional)'}
        value={text} onChange={event => setText(event.currentTarget.value)} maxLength={500} disabled={saving} />
      {failed && <Text size="sm" c="red" role="alert">Your reason was not saved. Try again.</Text>}
      <Group gap="xs"><Button size="xs" type="submit" loading={saving} disabled={!text.trim()}>Save reason</Button>
        <Button size="xs" variant="subtle" color="dark" disabled={saving} onClick={close}>Skip</Button></Group>
    </Stack>
  </form>;
}

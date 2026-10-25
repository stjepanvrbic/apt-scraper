import { useEffect, useRef } from 'react';
import { Alert, Button, Group, Loader, Paper, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconArrowsDiff } from '@tabler/icons-react';
import { ListingCard } from './ListingCard';
import { useCompare } from './useGallery';

export function Compare({ open }: { open: (id: string) => void }) {
  const { query, mutation, pending, busy, announcement, save } = useCompare();
  const heading = useRef<HTMLHeadingElement>(null);
  const focusedAction = useRef(0);
  const question = query.data?.question;
  const answered = query.data?.answered;
  useEffect(() => {
    if (announcement && !busy && focusedAction.current !== mutation.submittedAt) {
      focusedAction.current = mutation.submittedAt;
      heading.current?.focus();
    }
  }, [announcement, busy, mutation.submittedAt]);

  return <Stack gap="lg" maw={960} mx="auto">
    <Group justify="space-between" align="flex-start">
      <div><Title ref={heading} tabIndex={-1} order={2} size="h3">Which of these two?</Title>
        <Text c="dimmed" size="sm" mt="xs">Choose the place you would rather live in, or skip if you are unsure.</Text>
        <Text c="dimmed" size="sm">Your choices apply at the next rerank.</Text></div>
      {answered !== undefined && <Text size="sm" c="dimmed">{answered} {answered === 1 ? 'choice' : 'choices'} saved</Text>}
    </Group>
    <Text role="status" aria-live="polite" size="sm" c="dimmed" mih={20}>{announcement}</Text>
    {query.isError ? <Alert color="red" title="Could not load comparison">
      <Text size="sm">{query.error.message}</Text>
      <Button mt="sm" variant="light" color="red" loading={query.isFetching} onClick={() => query.refetch()}>Try again</Button>
    </Alert> : query.isPending || (busy && !question) ? <Group justify="center" py="xl"><Loader aria-label="Loading comparison" /></Group> : question ? <>
      {mutation.isError && <Alert color="red" title="Could not save">
        <Text size="sm">{mutation.error.message} Your comparison is still here.</Text>
        <Button mt="sm" color="red" variant="light" disabled={busy} onClick={() => save(mutation.variables!)}>Retry save</Button>
      </Alert>}
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="lg">
        {question.listings.map((listing, index) => <ListingCard key={listing.id} variant="compare" listing={listing}
          open={() => open(listing.id)} headingOrder={3} busy={busy}
          choosing={pending && mutation.variables?.action === 'answer' && mutation.variables.chosen === listing.id}
          choose={() => save({ action: 'answer', chosen: listing.id, other: question.listings[index === 0 ? 1 : 0].id })} />)}
      </SimpleGrid>
      <Group justify="center"><Button variant="subtle" color="dark" size="md" disabled={busy}
        loading={pending && mutation.variables?.action === 'skip'}
        onClick={() => save({ action: 'skip', first: question.listings[0].id, second: question.listings[1].id })}>Skip this pair</Button></Group>
    </> : <Paper p="xl" radius={20} withBorder><Stack align="center" gap="sm">
      <IconArrowsDiff size={32} stroke={1.4} aria-hidden="true" />
      <Title order={3} size="h3">No comparisons ready</Title>
      <Text c="dimmed" ta="center" maw={480}>You have seen all the pairs ready for now. New comparisons appear as more available listings are analyzed.</Text>
      <Button variant="light" loading={query.isFetching} onClick={() => query.refetch()}>Check again</Button>
    </Stack></Paper>}
  </Stack>;
}

import { Accordion, Anchor, Badge, Group, Paper, Stack, Table, Text, Title } from '@mantine/core';
import { IconCompass } from '@tabler/icons-react';
import type { Listing } from './model';

export function DistanceAttribution({ text }: { text: string | null }) {
  return text && /\b\d+(?:[.,]\d+)?\s*(?:km|kilomet(?:er|re)s?|mi|miles?|met(?:er|re)s?)\b/i.test(text) ?
    <Text size="xs" c="dimmed">© <Anchor href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer" size="xs" c="inherit">OpenStreetMap contributors</Anchor></Text> : null;
}

export function Exploration() {
  return <Group gap={6} wrap="nowrap" className="exploration-label"><IconCompass size={16} aria-hidden="true" style={{ flexShrink: 0 }} />
    <Text size="xs" fw={600}>Exploration · Shown to learn your taste</Text></Group>;
}

export const signed = (value: number) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 4, signDisplay: 'exceptZero' }).format(value);

export function RankingExplanation({ listing }: { listing: Listing }) {
  const explanation = listing.explanation;
  return <Paper p="md" withBorder radius="md" className="ranking-explanation">
    <Stack gap="sm">
      <Group justify="space-between"><Title order={3} size="h4">{listing.rank ? `Why it ranks #${listing.rank}` : 'Awaiting ranking'}</Title>
        {explanation && <Badge variant="light" color="gray" tt="none">Score {signed(explanation.score)}</Badge>}</Group>
      {listing.exploration && <Exploration />}
      <div><Text size="sm">{listing.reason ?? 'No ranking explanation yet. Score and rerank to update it.'}</Text><DistanceAttribution text={listing.reason} /></div>
      {explanation && <>
        <div><Text size="xs" c="dimmed" mb={2}>Most similar favorite</Text>
          <Text size="sm" fw={500}>{explanation.similar_favorite?.title ?? 'No other favorite to compare with yet.'}</Text></div>
        <Accordion variant="default" className="explanation-accordion">
          <Accordion.Item value="contributions">
            <Accordion.Control>All {explanation.contributions.length} feature contributions</Accordion.Control>
            <Accordion.Panel>
              <Text size="sm" c="dimmed" mb="md">Positive values raise the score; negative values lower it. These add up to the scoring model's score, before any review moves.</Text>
              <Table className="contribution-table" verticalSpacing="sm">
                <Table.Thead><Table.Tr><Table.Th>Feature and recorded value</Table.Th><Table.Th ta="right">Contribution</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>{explanation.contributions.map(part => <Table.Tr key={part.feature}>
                  <Table.Td><Text size="sm" fw={500}>{part.feature.replaceAll('_', ' ').replaceAll(':', ': ')}</Text>
                    <Text size="xs" c="dimmed">{part.value ?? 'No recorded value'}</Text><DistanceAttribution text={part.value} /></Table.Td>
                  <Table.Td ta="right"><Text size="sm" className="score-number" c={part.contribution > 0 ? 'var(--mantine-color-green-light-color)' : part.contribution < 0 ? 'red.9' : 'dimmed'}>{signed(part.contribution)}</Text></Table.Td>
                </Table.Tr>)}</Table.Tbody>
              </Table>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      </>}
    </Stack>
  </Paper>;
}

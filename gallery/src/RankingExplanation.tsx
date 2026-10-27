import { Accordion, Anchor, Badge, Group, Paper, Stack, Table, Text, Title } from '@mantine/core';
import { IconCompass } from '@tabler/icons-react';
import type { Listing, ListingSummary, Reason } from './model';

function OpenStreetMapCredit() {
  return <Text size="xs" c="dimmed">© <Anchor href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer" size="xs" c="inherit">OpenStreetMap contributors</Anchor></Text>;
}

const distance = /\b\d+(?:[.,]\d+)?\s*(?:km|kilomet(?:er|re)s?|mi|miles?|met(?:er|re)s?)\b/i;
const reasonTexts = (reason: Reason) => 'text' in reason.features ? [reason.features.text] : [...reason.features.for, ...reason.features.against];

export function DistanceAttribution({ texts }: { texts: string[] }) {
  return texts.some(text => distance.test(text)) ? <OpenStreetMapCredit /> : null;
}

function ValueList({ label, values }: { label: string; values: string[] }) {
  return <div className="reason-values"><Text span size="sm" fw={600}>{label}: </Text>
    <ul role="list" aria-label={label}>{values.map((value, index) => <li key={index}><Text span size="sm">{value}</Text></li>)}</ul></div>;
}

export function ListingReason({ listing, fallback }: { listing: ListingSummary; fallback: string }) {
  const reason = listing.reason;
  if (!reason) return <Text size="sm">{listing.unranked_reason ?? fallback}</Text>;
  const { features } = reason;
  return <Stack gap={4}>
    {reason.code_only && <Text size="sm">Ranked from code features only, because extraction failed 3 times.</Text>}
    {'text' in features ? <Text size="sm">{features.text}</Text> : features.for.length || features.against.length ? <>
      {!!features.for.length && <ValueList label="For it" values={features.for} />}
      {!!features.against.length && <ValueList label="Against it" values={features.against} />}
    </> : <Text size="sm">Close to the average listing.</Text>}
    {reason.similar_favorite && <Text size="sm">Most like your favorite {reason.similar_favorite}.</Text>}
    <DistanceAttribution texts={reasonTexts(reason)} />
  </Stack>;
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
      <ListingReason listing={listing} fallback="No ranking explanation yet. Score and rerank to update it." />
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
                    <Text size="xs" c="dimmed">{part.value ?? 'No recorded value'}</Text>{part.feature.startsWith('place:') && <OpenStreetMapCredit />}</Table.Td>
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

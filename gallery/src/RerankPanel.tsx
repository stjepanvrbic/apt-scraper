import { Accordion, Alert, Badge, Button, Divider, Group, Loader, Paper, ScrollArea, Stack, Table, Text, Title } from '@mantine/core';
import { IconArrowsSort, IconCheck, IconHeart, IconNotes } from '@tabler/icons-react';
import type { Review, ReviewResult } from './model';
import { DistanceAttribution } from './RankingExplanation';
import type { useRerank } from './useRerank';

const checkValue = (value: number | null) => value === null ? 'No favorites' : new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
const reviewLabels = { waiting: 'Waiting for review', running: 'Review in progress', done: 'Review complete', failed: 'Review failed', superseded: 'Review superseded' };

function ReviewText({ text }: { text: string }) {
  return <div><Text size="sm" className="preserve-lines">{text}</Text><DistanceAttribution texts={[text]} /></div>;
}

function ReviewDetails({ result, open }: { result: ReviewResult; open: (id: string) => void }) {
  return <Stack gap="md">
    <Alert color={result.outcome === 'reverted' ? 'gray' : 'blue'} title={result.outcome === 'kept' ? 'Review moves kept' : result.outcome === 'reverted' ? 'Review moves reverted' : 'No rank changes'}>
      {result.reverted_because ?? (result.outcome === 'kept' ? 'The moves passed the favorite check.' : 'The review kept the scoring order.')}
      <Text size="sm" mt={6}>{result.reasons} {result.reasons === 1 ? 'reason' : 'reasons'} rewritten.</Text>
    </Alert>
    <div><Text size="sm" fw={600} mb="xs">Favorite check before and after proposed moves</Text>
      <Table verticalSpacing="xs"><Table.Thead><Table.Tr><Table.Th>Held-out rank</Table.Th><Table.Th>Before</Table.Th><Table.Th>After</Table.Th></Table.Tr></Table.Thead>
        <Table.Tbody>{(['mean', 'median'] as const).map(metric => <Table.Tr key={metric}><Table.Th scope="row" fw={500}>{metric === 'mean' ? 'Mean' : 'Median'}</Table.Th>
          <Table.Td>{checkValue(result.check.before[metric])}</Table.Td><Table.Td>{checkValue(result.check.after[metric])}</Table.Td></Table.Tr>)}</Table.Tbody></Table>
      <Text size="xs" c="dimmed" mt="xs">Lower is better. Reverted moves do not change your rankings.</Text></div>
    {!!result.moves.length && <Stack gap="sm"><Text size="sm" fw={600}>{result.outcome === 'reverted' ? 'Reverted moves' : 'Moves'}</Text>
      {result.moves.map(move => <Paper key={move.id} p="sm" withBorder>
        <Group justify="space-between" gap="xs"><Button variant="subtle" size="compact-sm" className="listing-result-link" onClick={() => open(move.id)}>{move.title ?? move.id}</Button>
          <Badge variant="light" color={result.outcome === 'reverted' ? 'gray' : 'blue'} tt="none">#{move.from} → #{move.to}</Badge></Group>
        <ReviewText text={move.reason} /><Text size="xs" c="dimmed" mt={4}>Confidence {Math.round(move.confidence * 100)}%</Text>
      </Paper>)}</Stack>}
    {!!result.flags.length && <Stack gap="sm"><Text size="sm" fw={600}>Preference conflicts</Text>
      {result.flags.map((flag, index) => <Paper key={`${flag.id}-${index}`} p="sm" withBorder>
        <Button variant="subtle" size="compact-sm" className="listing-result-link" onClick={() => open(flag.id)}>{flag.title ?? flag.id}</Button>
        <Text size="sm" mt={4}>Ranks against what you {flag.kind === 'like' ? 'like' : 'dislike'}: {flag.preference}</Text>
      </Paper>)}</Stack>}
    {!!result.findings.length && <div><Text size="sm" fw={600} mb="xs">Review findings</Text><Stack gap="xs">{result.findings.map((finding, index) => <ReviewText key={index} text={finding} />)}</Stack></div>}
  </Stack>;
}

function ReviewStatus({ review }: { review: Review | null }) {
  return <Group gap="xs" wrap="nowrap" aria-live="polite">
    {review?.state === 'running' ? <Loader size={14} aria-hidden="true" /> : review?.state === 'done' ? <IconCheck size={16} aria-hidden="true" /> : <IconNotes size={16} aria-hidden="true" />}
    <Text size="sm" fw={500}>{review ? reviewLabels[review.state] : 'No review recorded'}</Text>
  </Group>;
}

export function RerankPanel({ ranking, open, busy, galleryReadError }: { ranking: ReturnType<typeof useRerank>; open: (id: string) => void; busy: boolean; galleryReadError: string }) {
  const { query, mutation, run, running } = ranking;
  const summary = query.data?.last_rerank;
  const review = summary?.review;
  const hypotheses = query.data?.hypotheses ?? [];
  return <Paper component="section" aria-label="Ranking controls and results" radius={20} withBorder p="lg" mb="lg" className="rerank-panel">
    <Stack gap="md">
      <Group justify="space-between" align="center" className="rerank-heading">
        <div><Title order={2} size="h4">Your latest ranking</Title>
          <Text size="sm" c="dimmed" mt={4}>Apply your favorites, choices and preferences to every match.</Text></div>
        <Button leftSection={<IconArrowsSort size={18} />} size="md" loading={running} disabled={running || busy || !query.isSuccess} onClick={run}>
          {running ? 'Scoring and reranking' : mutation.isError ? 'Retry rerank' : 'Score and rerank'}</Button>
      </Group>
      {query.isPending && <Group gap="xs"><Loader size="sm" aria-label="Loading ranking status" /><Text size="sm" c="dimmed">Checking ranking status…</Text></Group>}
      {query.isError && query.error.message !== galleryReadError && <Alert color="red" title="Could not load ranking status">
        {query.error.message}{summary && <Text size="sm" mt="xs">Showing the last loaded result. Review updates may be missing.</Text>}
        <Button mt="sm" size="xs" variant="light" color="red" loading={query.isFetching} onClick={() => void query.refetch()}>Retry ranking status</Button>
      </Alert>}
      {mutation.isError && <Alert color="red" title="Could not rerank">{mutation.error.message}</Alert>}
      {summary?.labels !== undefined && <Text size="sm" c="dimmed">Learning from {summary.labels} {summary.labels === 1 ? 'label' : 'labels'}</Text>}
      {!!hypotheses.length && <Stack component="section" aria-label="Preference guesses" gap="sm">
        <Title order={3} size="h5">Preference guesses</Title>
        <Text size="sm" c="dimmed">These are the model's guesses. Confirm one to make it a stated preference.</Text>
        {hypotheses.map(item => <Group key={item.id} justify="space-between" align="start" gap="sm">
          <Text size="sm" style={{ flex: '1 1 200px', minWidth: 0, overflowWrap: 'anywhere' }}>You may {item.direction}: {item.phrase}</Text>
          <Group gap="xs"><Button size="xs" variant="light" disabled={busy || ranking.hypothesis.isPending}
            loading={ranking.hypothesis.isPending && ranking.hypothesis.variables?.id === item.id && ranking.hypothesis.variables.decision === 'confirm'}
            aria-label={`Confirm preference: ${item.phrase}`} onClick={() => ranking.hypothesis.mutate({ id: item.id, decision: 'confirm' })}>Confirm preference</Button>
            <Button size="xs" variant="subtle" color="dark" disabled={busy || ranking.hypothesis.isPending}
              loading={ranking.hypothesis.isPending && ranking.hypothesis.variables?.id === item.id && ranking.hypothesis.variables.decision === 'reject'}
              aria-label={`Reject guess: ${item.phrase}`} onClick={() => ranking.hypothesis.mutate({ id: item.id, decision: 'reject' })}>Reject guess</Button></Group>
        </Group>)}
      </Stack>}
      {ranking.hypothesis.isError && <Alert color="red" title="Could not save your decision">{ranking.hypothesis.error.message}</Alert>}
      <div role="status" aria-live="polite" aria-atomic="true">
        {running ? <Text size="sm">Scoring your listings and checking each favorite…</Text> : summary ?
          <Group gap="sm"><Badge variant="light" tt="none" color="gray">{summary.ranked} ranked</Badge>
            {!!summary.waiting_for_features && <Text size="xs" c="dimmed">{summary.waiting_for_features} waiting for features</Text>}
            <Text size="xs" c="dimmed">Updated {new Date(summary.finished_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</Text>
            {review?.state === 'done' && <Badge variant="light" color={review.result.outcome === 'reverted' ? 'gray' : 'blue'} tt="none">
              {review.result.outcome === 'reverted' ? 'Moves reverted' : review.result.outcome === 'kept' ? 'Moves kept' : 'No rank changes'}</Badge>}
          </Group> : query.isSuccess ? <Text size="sm" c="dimmed">No rerank yet</Text> : null}
      </div>
      {summary && <>
        {!!summary.stale?.length && <Text size="sm">{summary.stale.length} {summary.stale.length === 1 ? 'listing changed' : 'listings changed'} during scoring. Rerank again to include the latest content.</Text>}
        <Accordion key={summary.id} multiple defaultValue={['favorites']} variant="default" order={3} className="ranking-results">
          <Accordion.Item value="favorites">
            <Accordion.Control icon={<IconHeart size={18} aria-hidden="true" />}>Favorite check <Text component="span" size="sm" c="dimmed">· {summary.held_out.length} {summary.held_out.length === 1 ? 'favorite' : 'favorites'}</Text></Accordion.Control>
            <Accordion.Panel>
              {summary.held_out.length ? <>
                <Text size="sm" c="dimmed" mb="sm">Each favorite's rank when its own feedback is held out. Lower ranks mean your other choices explain it better.</Text>
                <Group gap="lg" mb="sm"><Text size="sm">Median <b>{checkValue(summary.held_out_median)}</b></Text><Text size="sm">Mean <b>{checkValue(summary.held_out_mean)}</b></Text></Group>
                <ScrollArea.Autosize mah={190} type="auto" scrollbars="y" offsetScrollbars="y" viewportProps={{ role: 'region', 'aria-label': 'All favorites and held-out ranks', tabIndex: 0 }}>
                  <Table stickyHeader verticalSpacing="xs"><Table.Thead><Table.Tr><Table.Th>Favorite</Table.Th><Table.Th ta="right" w={120}>Held-out rank</Table.Th></Table.Tr></Table.Thead>
                    <Table.Tbody>{summary.held_out.map(favorite => <Table.Tr key={favorite.id}>
                      <Table.Td><Button variant="subtle" color="dark" size="compact-sm" className="listing-result-link" onClick={() => open(favorite.id)}>{favorite.title}</Button></Table.Td>
                      <Table.Td ta="right" className="score-number" data-testid="held-out-rank">#{favorite.rank}</Table.Td>
                    </Table.Tr>)}</Table.Tbody></Table>
                </ScrollArea.Autosize>
              </> : <Text size="sm" c="dimmed">No favorites to check yet. Save a few places you like, then rerank to see how well your choices explain them.</Text>}
            </Accordion.Panel>
          </Accordion.Item>
          <Accordion.Item value="review">
            <Accordion.Control><ReviewStatus review={review ?? null} /></Accordion.Control>
            <Accordion.Panel>
              {!review && <Text size="sm" c="dimmed">This ranking has no stored review.</Text>}
              {review?.state === 'waiting' && <Text size="sm" c="dimmed">The scheduled review will check these rankings, usually within a minute. You can keep browsing. Results appear here automatically.</Text>}
              {review?.state === 'running' && <Text size="sm" c="dimmed">The review is checking reasons and proposed rank changes. Your scoring results are ready above.</Text>}
              {review?.state === 'superseded' && <Text size="sm" c="dimmed">A newer rerank replaced this review.</Text>}
              {review?.state === 'failed' && <Alert color="red" title="Review could not finish">{review.result.error}<Text size="sm" mt="xs">Your scoring results are saved. Score and rerank again to request a fresh review.</Text></Alert>}
              {review?.state === 'done' && <ReviewDetails result={review.result} open={open} />}
            </Accordion.Panel>
          </Accordion.Item>
          {!!summary.findings.length && <Accordion.Item value="findings"><Accordion.Control>Scoring findings · {summary.findings.length}</Accordion.Control>
            <Accordion.Panel><Stack gap="xs">{summary.findings.map((finding, index) => <ReviewText key={index} text={finding} />)}</Stack></Accordion.Panel></Accordion.Item>}
        </Accordion>
      </>}
      {review && ['waiting', 'running'].includes(review.state) && <><Divider /><Text size="xs" c="dimmed">You can rerank again while the review is pending. A new rerank replaces that review.</Text></>}
    </Stack>
  </Paper>;
}

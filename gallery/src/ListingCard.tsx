import { useState } from 'react';
import { ActionIcon, AspectRatio, Badge, Box, Button, Card, Divider, Group, Image, Paper, Stack, Text, Title, UnstyledButton } from '@mantine/core';
import { IconHeart, IconPhoto } from '@tabler/icons-react';
import { period, price, title, viewingTime, type ListingSummary } from './model';
import { DistanceAttribution, Exploration } from './RankingExplanation';

export type Save = (path: string, body: Record<string, unknown>) => Promise<boolean>;
export const listingPath = (listing: ListingSummary, action: string) => `/api/listings/${encodeURIComponent(listing.id)}/${action}`;

export function Photo({ listing }: { listing: ListingSummary }) {
  const [failedSource, setFailedSource] = useState<string>();
  return <AspectRatio ratio={4 / 3}>
    {listing.photos.length && failedSource !== listing.photos[0] ? <Image src={listing.photos[0]} alt={title(listing)} loading="lazy" radius={20}
      onError={() => setFailedSource(listing.photos[0])} /> :
      <Paper bg="gray.1" radius={20} aria-hidden="true"><Stack align="center" gap="xs"><IconPhoto /><Text c="dimmed">{listing.photos.length ? 'Photo unavailable' : 'No photos available'}</Text></Stack></Paper>}
  </AspectRatio>;
}

type CardProps = { listing: ListingSummary; open: () => void; busy: boolean; headingOrder?: 2 | 3 } & (
  { variant?: 'browse'; save: Save } | { variant: 'compare'; choose: () => void; choosing: boolean }
);

export function ListingCard(props: CardProps) {
  const { listing, open, busy, headingOrder = 2 } = props;
  return <Card component="article" padding={0} radius={20} className="listing-card">
    <Box pos="relative">
    <UnstyledButton onClick={open} aria-label={`View ${title(listing)}`} className="photo-button">
      <Photo listing={listing} />
    </UnstyledButton>
      {props.variant !== 'compare' && <><Badge className="rank-badge" color="dark" variant="white" radius="sm">{listing.rank ? `#${listing.rank} match` : 'Awaiting ranking'}</Badge>
      <ActionIcon className="favorite-button" variant="white" color={listing.favorite ? 'pink' : 'dark'} size={44} radius="xl"
        aria-label={`${listing.favorite ? 'Unfavorite' : 'Favorite'} ${title(listing)}`} aria-pressed={listing.favorite}
        disabled={busy || listing.dismissed} onClick={() => props.save(listingPath(listing, 'feedback'), { favorite: !listing.favorite })}>
        <IconHeart size={22} fill={listing.favorite ? 'currentColor' : 'none'} />
      </ActionIcon></>}
    </Box>
    <Stack gap={8} p="md" style={{ flex: 1 }}>
      <Group justify="space-between"><Text fw={700} size="lg">{price(listing)} <Text component="span" fw={400} c="dimmed" size="sm">{period(listing, '/')}</Text></Text>
        {listing.id.startsWith('sample:') && <Text size="xs" c="dimmed">Sample</Text>}
      </Group>
      <UnstyledButton onClick={open}><Title order={headingOrder} size="md" fw={600}>{title(listing)}</Title></UnstyledButton>
      <Text size="sm" c="dimmed">{listing.rooms ?? 'Rooms not listed'} · {listing.size ?? 'Size not listed'}</Text>
      <Text size="sm" lineClamp={props.variant === 'compare' ? undefined : 1}>{listing.address ?? 'Address not listed'}</Text>
      {props.variant === 'compare' ? <Stack gap="xs" mt="auto" pt="sm">
        <Button size="md" fullWidth aria-label={`Choose this apartment: ${title(listing)}`} disabled={busy} loading={props.choosing} onClick={props.choose}>Choose this apartment</Button>
        <Button variant="subtle" color="dark" fullWidth onClick={open}>View details</Button>
      </Stack> : <><Divider />
      {listing.exploration && <Exploration />}
      <div><Text size="sm" c="dimmed" mih={42}>{listing.reason ?? 'Awaiting ranking.'}</Text><DistanceAttribution text={listing.reason} /></div>
      <Badge variant="light" color={listing.status === 'available' ? 'green' : 'gray'}>{listing.status === 'available' ? 'Available' : 'Unavailable'}</Badge>
      {listing.viewing && <Text size="sm" fw={600}>{viewingTime(listing.viewing.time)}</Text>}
      {listing.contact && <Badge variant="light" color={listing.contact.status === 'pending' ? 'gray' : 'blue'}>
        {({pending: 'Contact requested', in_progress: 'Contact in progress', contacted: 'Contacted', failed: 'Contact failed', closed: 'Contact closed'})[listing.contact.status]}
      </Badge>}
      {!!listing.correspondence.length && <Stack gap="xs"><Text size="sm" fw={600}>Latest correspondence</Text>
        {listing.correspondence.map(message => <div key={message.id}><Text size="xs" c="dimmed">{message.direction === 'incoming' ? 'Listing agent' : 'You'} · {message.channel}</Text><Text size="sm" className="preserve-lines">{message.text}</Text></div>)}
        <Button variant="subtle" onClick={open}>View all {listing.correspondence_total} messages</Button></Stack>}</>}
    </Stack>
  </Card>;
}


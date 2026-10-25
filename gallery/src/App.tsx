import { lazy, Suspense, useState } from 'react';
import {
  Alert, Anchor, Badge, Box, Button, Container, Divider, Drawer,
  Group, Loader, Paper, Pagination, SimpleGrid, Stack, Tabs, Text, Textarea, Title,
} from '@mantine/core';
import { IconArrowsDiff, IconArrowUpRight, IconBuilding, IconCalendar, IconHeart, IconLayoutGrid, IconMessageCircle, IconNotes, IconPhoto, IconRefresh } from '@tabler/icons-react';
import Lightbox from 'yet-another-react-lightbox';
import Counter from 'yet-another-react-lightbox/plugins/counter';
import { date, period, price, title, viewingTime } from './model';
import { useGallery, useListing } from './useGallery';
import { ListingCard, Photo, listingPath, type Save } from './ListingCard';
import { Compare } from './Compare';

const NotesChat = lazy(() => import('./NotesChat'));

const tabs = [
  { name: 'Rankings', icon: IconLayoutGrid, description: 'Apartments ordered by how well they fit you.' },
  { name: 'Favorites', icon: IconHeart, description: 'The places you want to keep coming back to.' },
  { name: 'Compare', icon: IconArrowsDiff, description: 'Help your rankings reflect what you like.' },
  { name: 'Contacted', icon: IconMessageCircle, description: 'Your contact requests and listing correspondence.' },
  { name: 'Viewings', icon: IconCalendar, description: 'Booked visits, with the details in one place.' },
  { name: 'Notes', icon: IconNotes, description: 'Tell Hermes what makes a place feel right for you.' },
];

function NoteForm({ save, path }: { save: Save; path: string }) {
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);
  return <form onSubmit={async event => {
    event.preventDefault();
    if (!text.trim() || saving) return;
    setSaving(true);
    if (await save(path, { text })) setText('');
    setSaving(false);
  }}>
    <Textarea label="Your note" value={text} onChange={event => setText(event.currentTarget.value)}
      placeholder="What do you like? What would you check at a viewing?" autosize minRows={3} maxRows={8} maxLength={20000} />
    <Button type="submit" mt="sm" loading={saving} disabled={!text.trim()}>Save note</Button>
  </form>;
}

function Details({ id, close, save, busy, saveError, readError, compare }: { compare: boolean; id: string | undefined; close: () => void; save: Save; busy: boolean; saveError: string; readError: string }) {
  const [photo, setPhoto] = useState(-1);
  const [pages, setPages] = useState({ notes_page: 1, correspondence_page: 1, viewings_page: 1 });
  const details = useListing(id, pages);
  const listing = details.data;
  return <>
    <Drawer opened={!!id} onClose={() => { setPhoto(-1); setPages({ notes_page: 1, correspondence_page: 1, viewings_page: 1 }); close(); }} position="right" size="min(760px, 100%)" padding="xl"
      attributes={{ header: { role: 'presentation' } }}
      title="Listing details" closeButtonProps={{ 'aria-label': 'Close listing details' }}
      trapFocus={photo < 0} closeOnEscape={photo < 0}>
      {details.isLoading && <Loader aria-label="Loading listing" />}
      {(details.error || readError) && <Alert color="red" title="Could not load listing">{details.error?.message || readError}</Alert>}
      {listing && <Stack gap="lg">
        <div><Group gap="xs" mb="xs"><Badge variant="light" color={listing.status === 'available' ? 'green' : 'gray'}>
          {listing.status === 'available' ? 'Available' : 'Unavailable'}</Badge>
          {listing.dismissed && <Badge color="gray">Dismissed</Badge>}</Group>
          <Title order={2}>{title(listing)}</Title><Text c="dimmed" mt="xs">{listing.address ?? 'Address not listed'}</Text></div>
        {saveError && <Alert color="red" title="Could not save">{saveError}</Alert>}
        <Box pos="relative"><Photo listing={listing} />
          {!!listing.photos.length && <Button className="all-photos" variant="white" color="dark" leftSection={<IconPhoto size={16} />}
            onClick={() => setPhoto(0)}>View all {listing.photos.length} photos</Button>}
        </Box>
        <Group justify="space-between"><Text size="xl" fw={700}>{price(listing)}</Text><Text c="dimmed">{period(listing, 'per')}</Text></Group>
        <Group><Text>{listing.rooms ?? 'Rooms not listed'}</Text><Divider orientation="vertical" /><Text>{listing.size ?? 'Size not listed'}</Text></Group>
        <Group>
          <Button leftSection={<IconMessageCircle size={18} />} disabled={busy || (!!listing.contact && listing.contact.status !== 'failed')}
            onClick={() => save(listingPath(listing, 'contact'), {})}>{listing.contact?.status === 'failed' ? 'Retry contact' : listing.contact ? 'Contact requested' : 'Request contact'}</Button>
          <Button variant="light" color={listing.favorite ? 'pink' : 'dark'} leftSection={<IconHeart size={18} />}
            disabled={busy || listing.dismissed} onClick={() => save(listingPath(listing, 'feedback'), { favorite: !listing.favorite })}>
            {listing.favorite ? 'Unfavorite' : 'Favorite'}</Button>
          <Button variant="subtle" color="gray" disabled={busy}
            onClick={() => save(listingPath(listing, 'feedback'), { dismissed: !listing.dismissed })}>
            {listing.dismissed ? 'Restore listing' : 'Dismiss'}</Button>
        </Group>
        {listing.contact?.status === 'failed' && <Alert color="red" title="Contact failed">{listing.contact.error}</Alert>}
        {listing.contact?.status === 'pending' && <Text size="sm" c="dimmed">Request saved. No message has been sent yet.</Text>}
        {listing.dismissed && <Text size="sm" c="dimmed">Hidden from Rankings and Favorites. Your notes and contact history are kept.</Text>}
        {!compare && <Paper p="md" bg="gray.0" radius="md"><Text size="sm" fw={600} mb={4}>{listing.analysis_current && listing.rank ? `Why it ranks #${listing.rank}` : 'Awaiting ranking'}</Text><Text size="sm">{listing.analysis_current ? listing.reason : 'The listing is waiting for analysis of its current content.'}</Text></Paper>}
        <div><Title order={3} size="h4" mb="xs">About this apartment</Title><Text className="preserve-lines">{listing.description ?? 'No description provided.'}</Text></div>
        <div><Title order={3} size="h4" mb="sm">Amenities</Title><Group gap="xs">{listing.amenities.map(amenity =>
          <Badge key={amenity} variant="light" color="gray" size="lg" tt="none" fw={400}>{amenity}</Badge>)}
          {!listing.amenities.length && <Text c="dimmed">No amenities listed.</Text>}</Group></div>
        <Text size="sm" c="dimmed">First seen {date(listing.first_seen)}</Text>
        <div><Title order={3} size="h4" mb="xs">Original listings</Title><Stack gap="xs">{listing.sources.map(source =>
          <Anchor key={source.url} href={source.url} target="_blank" rel="noopener noreferrer">{source.label} <IconArrowUpRight size={14} /></Anchor>)}</Stack></div>
        {listing.viewings.map(viewing => <Paper key={viewing.id} p="md" bg="blue.0" radius="md"><Text size="sm" fw={600}>{({booked: 'Booked viewing', agreed: 'Agreed viewing, not yet in your calendar', canceled: 'Canceled viewing'})[viewing.status]}</Text>
          <Text fw={600} mt={4}>{viewingTime(viewing.time)}</Text><Text mt="xs" className="preserve-lines">{viewing.details}</Text></Paper>)}
        {listing.viewings_total > 50 && <Pagination total={Math.ceil(listing.viewings_total / 50)} value={pages.viewings_page} onChange={value => setPages({ ...pages, viewings_page: value })} aria-label="Viewing history pages" />}
        {listing.contact && <div><Title order={3} size="h4" mb="sm">Correspondence</Title><Stack gap="sm">
          {!listing.correspondence.length && <Text c="dimmed" size="sm">{listing.id.startsWith('sample:') ? 'Sample listings are never contacted.' : 'No correspondence yet. Hermes sends the first message within a few minutes while its gateway runs.'}</Text>}
          {listing.correspondence.map(message => <Paper key={message.id} p="md" withBorder>
            <Text size="xs" c="dimmed" mb="xs">{message.direction === 'incoming' ? 'Listing agent' : 'You'} · {message.channel} · {date(message.created_at)}</Text>
            <Text className="preserve-lines">{message.text}</Text></Paper>)}
        </Stack>{listing.correspondence_total > 50 && <Pagination mt="md" total={Math.ceil(listing.correspondence_total / 50)} value={pages.correspondence_page} onChange={value => setPages({ ...pages, correspondence_page: value })} aria-label="Correspondence pages" />}</div>}
        <Divider />
        <div><Title order={3} size="h4" mb="md">Listing notes</Title><Stack mb="md">{listing.notes.map(note =>
          <Paper key={note.id} p="md" withBorder><Text className="preserve-lines">{note.text}</Text><Text size="xs" c="dimmed" mt={4}>{note.role === 'agent' ? 'Hermes' : 'You'} · {date(note.created_at)}</Text></Paper>)}</Stack>
          {listing.notes_total > 50 && <Pagination mb="md" total={Math.ceil(listing.notes_total / 50)} value={pages.notes_page} onChange={value => setPages({ ...pages, notes_page: value })} aria-label="Listing note pages" />}
          <NoteForm key={listing.id} path={listingPath(listing, 'notes')} save={save} /></div>
      </Stack>}
    </Drawer>
    {listing && <Lightbox open={photo >= 0} close={() => setPhoto(-1)} index={photo}
      slides={listing.photos.map((src, index) => ({ src, alt: `${title(listing)}, photo ${index + 1}` }))}
      plugins={[Counter]} counter={{ container: { style: { top: 'unset', bottom: 16 } } }} />}
  </>;
}

export default function App() {
  const [tab, setTab] = useState<string | null>('Rankings');
  const [selected, setSelected] = useState<string>();
  const [page, setPage] = useState(1);
  const [dismissed, setDismissed] = useState(false);
  const { state, readError, saveError, busy, loading, refresh, save } = useGallery(dismissed ? 'Dismissed' : tab!, page);
  const listings = state?.items ?? [];
  const awaiting = listings.filter(listing => !listing.analysis_current || !listing.rank);
  const groups = tab === 'Rankings' && !dismissed ? [
    { heading: null, items: listings.filter(listing => listing.analysis_current && listing.rank) },
    { heading: 'Awaiting ranking', items: awaiting },
  ] : [{ heading: null, items: listings }];
  const current = tabs.find(item => item.name === tab)!;
  return <>
    <Paper component="header" radius={0} className="site-header"><Container size="xl"><Group justify="space-between" py="lg">
      <Group gap="sm"><IconBuilding size={28} stroke={1.6} /><div><Text fw={700}>Apartment gallery</Text><Text size="xs" c="dimmed">Your search, in one place</Text></div></Group>
      <Group><Badge variant="light" color="gray" tt="none">Saved on this computer</Badge>
        <Button variant="subtle" color="dark" loading={loading} disabled={busy} leftSection={<IconRefresh size={16} />} onClick={refresh}>Refresh listings</Button></Group>
    </Group></Container></Paper>
    <Container component="main" size="xl" py="xl">
      <Tabs value={tab} onChange={value => { setTab(value); setPage(1); setDismissed(false); }} keepMounted={false}>
        <Tabs.List mb={32}>{tabs.map(item => <Tabs.Tab key={item.name} value={item.name} leftSection={<item.icon size={18} />}>{item.name}</Tabs.Tab>)}</Tabs.List>
        <Group justify="space-between" align="flex-end" mb="xl"><div><Text size="xs" c="dimmed" fw={600} tt="uppercase" mb={6}>Your apartment search</Text>
          <Title order={1}>{dismissed ? 'Dismissed listings' : current.name}</Title><Text c="dimmed" mt="xs">{current.description}</Text></div>
          {tab !== 'Notes' && tab !== 'Compare' && <Text c="dimmed" size="sm">{state?.total ?? 0} {state?.total === 1 ? 'place' : 'places'}</Text>}</Group>
        {tab === 'Rankings' && <Button variant="subtle" mb="md" onClick={() => { setDismissed(!dismissed); setPage(1); }}>{dismissed ? 'Back to rankings' : 'Dismissed listings'}</Button>}
        {readError && <Alert color="red" title="Could not load gallery" mb="lg">{readError}</Alert>}
        {saveError && !selected && <Alert color="red" title="Could not save" mb="lg">{saveError}</Alert>}
        {!state && tab !== 'Notes' && tab !== 'Compare' && !readError && <Loader aria-label="Loading listings" />}
        {(state || tab === 'Notes' || tab === 'Compare') && tabs.map(item => <Tabs.Panel key={item.name} value={item.name}>
          {item.name === 'Compare' ? <Compare open={setSelected} /> : item.name === 'Notes' ? <Suspense fallback={<Loader aria-label="Loading conversation" />}><NotesChat /></Suspense> :
            listings.length ? <Stack gap="xl">{groups.filter(group => group.items.length).map(group => <div key={group.heading ?? 'listings'}>
              {group.heading && <Title order={2} size="h3" mb="md">{group.heading}</Title>}
              <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="xl">{group.items.map(listing =>
                <ListingCard key={listing.viewing?.id ?? listing.id} listing={listing} open={() => setSelected(listing.id)} save={save} busy={busy} headingOrder={group.heading ? 3 : 2} />)}</SimpleGrid>
            </div>)}</Stack> :
              <Paper p={48} radius={20} withBorder><Stack align="center" gap="sm"><current.icon size={32} stroke={1.4} />
                <Title order={2} size="h3">{tab === 'Favorites' ? 'No favorites yet' : tab === 'Contacted' ? 'No contact requests yet' : tab === 'Viewings' ? 'No viewings booked' : 'No available listings'}</Title>
                <Text c="dimmed" ta="center">{tab === 'Favorites' ? 'Save a place with the heart button to find it here.' :
                  tab === 'Contacted' ? 'Request contact from a listing to follow it here.' : tab === 'Viewings' ? 'Confirmed viewing times will appear here.' :
                    'Ask Hermes to search with your criteria. New matches will appear here.'}</Text>
              </Stack></Paper>}
        </Tabs.Panel>)}
      </Tabs>
      {state && state.total > state.page_size && <Pagination mt="xl" total={Math.ceil(state.total / state.page_size)} value={page} onChange={setPage} aria-label="Listing pages" />}
      {listings.some(listing => listing.id.startsWith('sample:')) && <Text size="xs" c="dimmed" mt="xl">
        Sample listings with stock photos. Addresses, prices and rankings are examples, not search results.</Text>}
    </Container>
    <Details compare={tab === 'Compare'} id={selected} close={() => setSelected(undefined)} save={save} busy={busy} saveError={saveError} readError={readError} />
  </>;
}

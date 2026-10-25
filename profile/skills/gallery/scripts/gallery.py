import argparse
import json
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urldefrag

from server import make_server
from launch import forget_connection, install_fingerprint, open_gallery, save_connection, stop_gallery
from store import ListingStore, store_path, validate_listing


def main():
    parser = argparse.ArgumentParser(description='Browse and manage the local apartment gallery.')
    parser.add_argument('--database', type=Path)
    parser.add_argument('--sample', action='store_true', help='Use the separate sample listing store.')
    parser.add_argument('--port', type=int, default=8765)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('export')
    commands.add_parser('status')
    commands.add_parser('serve')
    commands.add_parser('stop')
    opening = commands.add_parser('open')
    opening.add_argument('--no-browser', action='store_true')
    opening.add_argument('--redact-token', action='store_true', help='Print only the base URL while opening the authenticated link.')
    importing = commands.add_parser('import')
    importing.add_argument('file', type=Path)
    note = commands.add_parser('note')
    note.add_argument('text')
    note.add_argument('--listing', required=True)
    note.add_argument('--role', choices=['user', 'agent'], default='agent')
    correspondence = commands.add_parser('correspond')
    correspondence.add_argument('listing')
    correspondence.add_argument('direction', choices=['incoming', 'outgoing'])
    correspondence.add_argument('channel')
    correspondence.add_argument('text')
    correspondence.add_argument('--message-id', required=True)
    correspondence.add_argument('--thread-id')
    arguments = parser.parse_args()
    path = arguments.database or store_path(arguments.sample)
    if arguments.command == 'stop':
        print(json.dumps({'stopped': stop_gallery(path, arguments.port)}))
        return
    if arguments.command == 'open':
        url = open_gallery(path, arguments.port, arguments.sample)
        if not arguments.no_browser and not webbrowser.open(url):
            raise RuntimeError('The gallery is running, but the browser did not open. Run open again to obtain its link.')
        print(json.dumps({'url': urldefrag(url)[0] if arguments.redact_token else url}))
        return
    store = ListingStore(path)
    if arguments.sample and not store.has_listings():
        for listing in json.loads((Path(__file__).resolve().parents[1] / 'sample.json').read_text(encoding='utf-8')):
            rank, reason = listing.pop('rank'), listing.pop('reason')
            result = store.upsert_listing(listing)
            store.analyze(listing['id'], result['content_fingerprint'], {})
            store.rank(listing['id'], result['content_fingerprint'], rank, reason)
    if arguments.command == 'export':
        print(json.dumps(store.state()))
    elif arguments.command == 'status':
        print(json.dumps({'has_listings': store.has_listings(), 'schema_version': store.schema_version}))
    elif arguments.command == 'serve':
        server = make_server(store, arguments.port, install_id=install_fingerprint())
        record = save_connection(store.path, server)
        print(json.dumps({'url': record['url'] + '/#token=' + record['token']}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            forget_connection(store.path, server.server_port, server.access_token)
    elif arguments.command == 'import':
        listings = json.loads(arguments.file.read_text(encoding='utf-8'))
        if not isinstance(listings, list):
            raise ValueError('The import file must contain an array of listings.')
        for listing in listings:
            validate_listing(listing)
        for listing in listings:
            store.upsert_listing(listing)
        print(json.dumps({'imported': len(listings)}))
    elif arguments.command == 'note':
        store.add_note(arguments.text, listing_id=arguments.listing, role=arguments.role)
        print(json.dumps({'saved': True}))
    elif arguments.command == 'correspond':
        store.correspond(arguments.listing, arguments.direction, arguments.channel, arguments.text,
                         external_message_id=arguments.message_id, external_thread_id=arguments.thread_id)
        print(json.dumps({'saved': True}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

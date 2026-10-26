import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def install_fingerprint(root=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def secrets_directory():
    return Path.home() / '.secrets' / 'apt-scraper' / 'gallery'


def connection_file(database, port):
    identity = hashlib.sha256(f'{Path(database).resolve()}:{port}'.encode()).hexdigest()
    return secrets_directory() / f'{identity}.json'


def save_connection(database, server):
    path = connection_file(database, server.server_port)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.platform == 'win32':
        result = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True,
                                check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        user = next(csv.reader(result.stdout.splitlines()))[1]
        subprocess.run(['icacls', str(path.parent), '/inheritance:r', '/grant:r', f'*{user}:(OI)(CI)F'],
                       capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        path.parent.chmod(0o700)
    record = {'url': f'http://127.0.0.1:{server.server_port}', 'token': server.access_token}
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
        json.dump(record, file)
    os.replace(temporary, path)
    return record


def read_connection(database, port):
    path = connection_file(database, port)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def forget_connection(database, port, token):
    record = read_connection(database, port)
    if record and record['token'] == token:
        connection_file(database, port).unlink()


def api_request(record, path, body=None):
    request = Request(record['url'] + path, headers={'Authorization': 'Bearer ' + record['token'], 'Content-Type': 'application/json'},
                      data=json.dumps(body).encode() if body is not None else None)
    with urlopen(request, timeout=3) as response:
        return json.load(response)


def health(record):
    try:
        return api_request(record, '/api/health')
    except URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            return None
        raise RuntimeError('The saved gallery connection could not be authenticated or reached. Use a different port.') from error


def stop_gallery(database, port):
    record = read_connection(database, port)
    if not record or not health(record):
        return False
    details = health(record)
    if details.get('application') != 'apt-scraper-gallery' or details.get('database') != str(Path(database).resolve()):
        raise RuntimeError('The running application does not match this gallery database.')
    api_request(record, '/api/stop', {})
    for _ in range(30):
        if not read_connection(database, port):
            return True
        time.sleep(0.1)
    raise RuntimeError('The gallery accepted stop but has not exited.')


def open_gallery(database, port, sample=False):
    database = Path(database).resolve()
    if not 1 <= port <= 65535:
        raise ValueError('Opening the gallery requires a port between 1 and 65535.')
    fingerprint = install_fingerprint()
    record = read_connection(database, port)
    if record:
        details = health(record)
        if details:
            if details.get('application') != 'apt-scraper-gallery' or details.get('database') != str(database):
                raise RuntimeError('The running application does not match this gallery database.')
            if details.get('install_id') == fingerprint:
                return record['url'] + '/#token=' + record['token']
            stop_gallery(database, port)
    executable = Path(sys.executable)
    if sys.platform == 'win32':
        executable = executable.with_name('pythonw.exe')
        if not executable.is_file():
            raise RuntimeError(f'Windowless Python is missing: {executable}. Repair this Python installation.')
    log_path = database.parent / 'gallery.log'
    with log_path.open('ab') as log:
        options = {} if sys.platform == 'win32' else {'start_new_session': True}
        process = subprocess.Popen([str(executable), str(Path(__file__).with_name('gallery.py')), '--database', str(database),
                                    '--port', str(port), *(['--sample'] if sample else []), 'serve'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, **options)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f'Gallery failed to start. Read {log_path}.')
        current = read_connection(database, port)
        if current and (not record or current['token'] != record['token']):
            details = health(current)
            if details and details['install_id'] == fingerprint:
                return current['url'] + '/#token=' + current['token']
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=5)
    raise RuntimeError(f'Gallery did not become ready. Read {log_path}.')

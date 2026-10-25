function captureLaunchToken() {
  const token = new URLSearchParams(window.location.hash.slice(1)).get('token');
  if (!token) return false;
  sessionStorage.setItem('gallery-access', token);
  history.replaceState(null, '', window.location.pathname + window.location.search);
  return true;
}

captureLaunchToken();
window.addEventListener('hashchange', () => { if (captureLaunchToken()) window.location.reload(); });

export async function request<Result>(path: string, body?: unknown, signal?: AbortSignal): Promise<Result> {
  let response: Response;
  try {
    response = await fetch(path, {
      signal,
      headers: { Authorization: `Bearer ${sessionStorage.getItem('gallery-access') ?? ''}`, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) }),
    });
  } catch {
    throw new Error('Could not reach the gallery. Check that it is running, then try again.');
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error ?? 'The gallery request failed.');
  return result;
}

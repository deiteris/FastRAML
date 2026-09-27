/** An integrating frontend: its own shell, router, and selectable tree document. */

import { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { App, Viewer } from '../src/index';

function Host() {
  const [contents, setContents] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [alternate, setAlternate] = useState(false);
  const [asApp, setAsApp] = useState(false);
  const [invalid, setInvalid] = useState(false);
  useEffect(() => {
    fetch('/api.json')
      .then((response) => response.text())
      .then(setContents, (cause: Error) => setError(cause.message));
  }, []);
  // Simulate a second backend response without converting through JSON.parse,
  // which would round the sample's large integers.
  const selected = useMemo(
    () => invalid ? '{' : contents && alternate
      ? contents
          .replace('"title": "Bookstore API"', '"title": "Alternate API"')
          .replaceAll('One book in the catalogue.', 'An alternate book definition.')
      : contents,
    [contents, alternate, invalid],
  );
  return (
    <div className="app">
      <h4>Host interface</h4>
      <button type="button" onClick={() => setAlternate(!alternate)}>Switch API</button>
      <button type="button" onClick={() => setAsApp(!asApp)}>Use {asApp ? 'Viewer' : 'App'}</button>
      <button type="button" onClick={() => setInvalid(!invalid)}>Toggle invalid JSON</button>
      {error && <p role="alert">{error}</p>}
      {selected !== null && (asApp
        ? <App contents={selected} />
        : <MemoryRouter><Viewer contents={selected} /></MemoryRouter>)}
    </div>
  );
}

const root = document.getElementById('host-root');
if (!root) throw new Error('no #host-root');
createRoot(root).render(<Host />);

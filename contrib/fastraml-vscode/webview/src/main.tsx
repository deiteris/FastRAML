/**
 * The viewer (`viewer/`) inside the preview's webview.
 *
 * The page asks for its document once it is listening: `{ready: true}`. The
 * extension answers `{tree}`, the JSON text of `fastraml tree`, or `{error}`
 * when the document has no effective model; it sends either again after a save.
 * Text, not a decoded value, so large integers survive (`numbers.ts`). An
 * error keeps the last model on the page, under the message.
 */

import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from 'fastraml-viewer';
import './theme.css';

declare function acquireVsCodeApi(): { postMessage(message: unknown): void };

type Update = { tree: string; error?: never } | { error: string; tree?: never };

const vscode = acquireVsCodeApi();

function Host() {
  const [tree, setTree] = useState<string>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    const listener = ({ data }: MessageEvent<Update>) => {
      if (data.tree !== undefined) setTree(data.tree);
      setError(data.error);
    };
    window.addEventListener('message', listener);
    vscode.postMessage({ ready: true });
    return () => window.removeEventListener('message', listener);
  }, []);
  return (
    <>
      {error !== undefined && <div className="vscode-error">{error}</div>}
      {tree !== undefined && <App contents={tree} themeToggle={false} />}
    </>
  );
}

const root = document.getElementById('root');
if (!root) throw new Error('no #root in the page');
const shadow = root.attachShadow({ mode: 'open' });
const style = document.createElement('link');
style.rel = 'stylesheet';
style.href = root.dataset.style ?? '';
shadow.append(style);
createRoot(shadow).render(
  <StrictMode>
    <Host />
  </StrictMode>,
);

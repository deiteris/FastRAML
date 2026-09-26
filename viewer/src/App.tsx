/** The standalone SPA: load api.json and give the viewer its own hash routes. */

import { HashRouter } from 'react-router';
import { DEFAULT_SOURCE, loadDocument } from './load';
import { LoadedViewer, Viewer } from './Viewer';

const loadDefault = () => loadDocument(DEFAULT_SOURCE);

/** `contents` replaces the default fetch and can change without reloading the page. */
export default function App({ contents }: { contents?: string }) {
  return (
    <HashRouter>
      {contents === undefined ? <LoadedViewer load={loadDefault} managePage /> : <Viewer contents={contents} managePage />}
    </HashRouter>
  );
}

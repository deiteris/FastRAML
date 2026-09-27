/**
 * The package entry, for a host that bundles the viewer from source: `App`
 * with its own hash router, or `Viewer` under the host's router. Importing it
 * brings the stylesheets.
 */

import 'normalize.css';
import './tokens.css';
import './styles.css';

export { default as App } from './App';
export { LoadedViewer, Viewer } from './Viewer';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './index';

const root = document.getElementById('root');
if (!root) throw new Error('no #root in the page');
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

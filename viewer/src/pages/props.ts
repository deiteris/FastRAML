/** What every page takes: a document, and the index of what has a page. */

import type { Document, Index } from '../model';

export interface Props {
  document: Document;
  index: Index;
}

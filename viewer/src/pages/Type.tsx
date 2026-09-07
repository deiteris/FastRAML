/** One declared type, and one declared annotation type. */

import { useParams } from 'react-router';
import { ShapeView, restates } from '../components/Shape';
import { Usages } from '../components/Usages';
import { Chip, Empty } from '../components/ui';
import { spellingOf } from '../model';
import type { Props } from './props';

export function TypePage({ document, index }: Props) {
  const { file, name } = useParams();
  const shape = document.types[decodeURIComponent(file ?? '')]?.[decodeURIComponent(name ?? '')];
  if (!shape) return <Empty>No type named {name}.</Empty>;
  // The kind, then the expression only where it says something neither the kind
  // nor the `extends` line below does. `Publication` is an `object` and read
  // `object · object`; `Book` is written `type: Entity` and read `Entity ·
  // object` above a line reading `extends Entity` -- the same fact twice, once
  // without the link.
  //
  // No file. Which of a document's files a type was written in is a fact about
  // the source, not about the API: a reader cannot open it, and a library is
  // reached through the prefix its `uses:` bound, never through its path.
  const written = spellingOf(shape, index);
  const adds = written !== shape.type && !restates(shape, index);
  return (
    <article>
      <h1>{shape.name ?? name}</h1>
      <p className="subtitle">
        <Chip tone="type">{shape.type}</Chip>
        {adds && <Chip>{written}</Chip>}
      </p>
      <ShapeView shape={shape} index={index} hideType />
      <Usages document={document} address={shape.id} />
    </article>
  );
}

export function AnnotationTypePage({ document, index }: Props) {
  const { file, name } = useParams();
  const shape = document.annotation_types[decodeURIComponent(file ?? '')]?.[decodeURIComponent(name ?? '')];
  if (!shape) return <Empty>No annotation type named {name}.</Empty>;
  // No count of where it was applied. It said how many nodes carried the
  // annotation and what *kind* each was -- never which one -- so a reader who
  // wanted to go and look had nothing to follow, and a reader who did not was
  // told a number about the document's source.
  return (
    <article>
      <h1>({shape.name ?? name})</h1>
      <p className="subtitle">annotation type</p>
      <ShapeView shape={shape} index={index} hideType />
    </article>
  );
}

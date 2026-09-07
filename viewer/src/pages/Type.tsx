/** One declared type, and one declared annotation type. */

import { useParams } from 'react-router';
import { ShapeView, restates } from '../components/Shape';
import { Usages } from '../components/Usages';
import { Chip, Empty, Section } from '../components/ui';
import { spellingOf } from '../model';
import type { Props } from './props';

export function TypePage({ document, index }: Props) {
  const { file, name } = useParams();
  const shape = document.types[decodeURIComponent(file ?? '')]?.[decodeURIComponent(name ?? '')];
  if (!shape) return <Empty>No type named {name} in {file}.</Empty>;
  // The kind, then the expression only where it says something neither the kind
  // nor the `extends` line below does. `Publication` is an `object` and read
  // `object · object`; `Book` is written `type: Entity` and read `Entity ·
  // object` above a line reading `extends Entity` -- the same fact twice, once
  // without the link.
  const written = spellingOf(shape, index);
  const adds = written !== shape.type && !restates(shape, index);
  return (
    <article>
      <h1>{shape.name ?? name}</h1>
      <p className="subtitle">
        <code>{file}</code> · <Chip tone="type">{shape.type}</Chip>
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
  if (!shape) return <Empty>No annotation type named {name} in {file}.</Empty>;
  const applied = document.annotations.filter((one) => one.type === shape.id);
  return (
    <article>
      <h1>({shape.name ?? name})</h1>
      <p className="subtitle">
        <code>{file}</code> · annotation type
      </p>
      <ShapeView shape={shape} index={index} hideType />
      {applied.length > 0 && (
        <Section title={`Applied ${applied.length} time${applied.length === 1 ? '' : 's'}`}>
          <table className="properties">
            <thead>
              <tr>
                <th>Target kind</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {applied.map((one, at) => (
                <tr key={at}>
                  <td>
                    <Chip>{one.target}</Chip>
                  </td>
                  <td>
                    <code>{JSON.stringify(one.value)}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
    </article>
  );
}

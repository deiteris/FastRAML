/** The index pages: every type, every annotation type, every scheme. */

import { Link } from 'react-router';
import { ProseInline } from '../components/markdown';
import { Chip, Empty } from '../components/ui';
import { declarations, spellingOf } from '../model';
import type { Props } from './props';

export function TypeList({ document, index }: Props) {
  return <DeclarationList document={document} index={index} of="types" title="Types" />;
}

export function AnnotationTypeList({ document, index }: Props) {
  return <DeclarationList document={document} index={index} of="annotation_types" title="Annotation types" />;
}

function DeclarationList({ document, index, of, title }: Props & { of: 'types' | 'annotation_types'; title: string }) {
  const all = declarations(document[of]);
  if (all.length === 0) return <Empty>This document declares no {title.toLowerCase()}.</Empty>;
  return (
    <article>
      <h1>{title}</h1>
      <table className="properties">
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          {all.map(({ file, name, value }) => {
            const entry = index.get(value.id);
            return (
              <tr key={`${file}/${name}`}>
                <td className="property-name">{entry ? <Link to={entry.href}>{name}</Link> : name}</td>
                <td>
                  <Chip tone="type">{spellingOf(value, index)}</Chip>
                </td>
                <td className="property-description">
                  <ProseInline>{value.description}</ProseInline>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </article>
  );
}

export function SecurityList({ document, index }: Props) {
  const all = declarations(document.security_schemes);
  if (all.length === 0) return <Empty>This document declares no security schemes.</Empty>;
  return (
    <article>
      <h1>Security schemes</h1>
      <table className="properties">
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          {all.map(({ file, name, value }) => {
            const entry = index.get(value.id);
            return (
              <tr key={`${file}/${name}`}>
                <td className="property-name">{entry ? <Link to={entry.href}>{name}</Link> : name}</td>
                <td>
                  <Chip tone="type">{value.type}</Chip>
                </td>
                <td className="property-description">
                  <ProseInline>{value.description}</ProseInline>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </article>
  );
}

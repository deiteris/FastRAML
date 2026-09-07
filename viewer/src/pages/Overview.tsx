/** The root: what the API is, where it lives, and what it declares. */

import { Link } from 'react-router';
import { Annotations } from '../components/Annotations';
import { ParameterTable } from '../components/Parameters';
import { Chip, Disclosure, Empty, KeyValues, Prose, Section } from '../components/ui';
import { baseUriOf, declarations } from '../model';
import type { Props } from './props';

export function Overview({ document, index }: Props) {
  const api = document.entry_point;
  if (!api) return <Empty>This document has no root.</Empty>;
  const counts = {
    endpoints: Object.keys(document.endpoints).length,
    types: declarations(document.types).length,
    'annotation types': declarations(document.annotation_types).length,
    'security schemes': declarations(document.security_schemes).length,
  };
  return (
    <article>
      <h1>{api.title ?? 'Untitled'}</h1>
      <div className="badges">
        {api.version && <Chip tone="type">{api.version}</Chip>}
        <Chip>{api.kind}</Chip>
        {api.protocols?.map((protocol) => (
          <Chip key={protocol}>{protocol}</Chip>
        ))}
      </div>
      <Prose>{api.description}</Prose>
      <Prose>{api.usage}</Prose>

      <KeyValues
        rows={[
          // Resolved, with `{version}` filled in, because that is the string a
          // caller prefixes to every path -- and the one every endpoint page
          // shows in front of its own.
          ['Base URI', api.base_uri ? <code className="url">{baseUriOf(api)}</code> : null],
          ['As written', api.base_uri && baseUriOf(api) !== api.base_uri ? <code>{api.base_uri}</code> : null],
          ['Media types', api.media_types?.length ? api.media_types.join(', ') : null],
          ...Object.entries(counts).map(
            ([what, many]) => [what[0]!.toUpperCase() + what.slice(1), String(many)] as [string, string],
          ),
        ]}
      />

      <Annotations applied={api.annotations} index={index} />

      {/* `{tenant}` in the base URI is a value every caller has to supply, so a
          reader who cannot see it cannot build a request at all (docs/16 § 11.4). */}
      <ParameterTable title="Base URI parameters" parameters={api.base_uri_parameters} index={index} />

      {api.documentation && api.documentation.length > 0 && (
        <Section title="Documentation">
          {api.documentation.map((item, at) => (
            <Disclosure key={at} summary={<strong>{item.title}</strong>} open={api.documentation!.length === 1}>
              <pre className="prose-block">{item.content}</pre>
            </Disclosure>
          ))}
        </Section>
      )}

      {document.annotations.length > 0 && (
        <Section title="Annotations applied in this document">
          <table className="properties">
            <thead>
              <tr>
                <th>Annotation</th>
                <th>Target</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {document.annotations.map((applied, at) => {
                const entry = index.get(applied.type);
                return (
                  <tr key={at}>
                    <td className="property-name">
                      {entry ? <Link to={entry.href}>({applied.name})</Link> : <code>({applied.name})</code>}
                    </td>
                    <td>
                      <Chip>{applied.target}</Chip>
                    </td>
                    <td>
                      <code>{JSON.stringify(applied.value)}</code>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Section>
      )}
    </article>
  );
}

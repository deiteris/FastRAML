/** The root: what the API is, where it lives, and what it declares. */

import { Link } from 'react-router';
import { Annotations } from '../components/Metadata';
import { ParameterTable } from '../components/Parameters';
import { Prose } from '../components/markdown';
import { SecuredByList } from '../components/Security';
import { oneLine } from '../components/json';
import { Chip, Empty, KeyValues, Section } from '../components/ui';
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

      {/* `securedBy:` at the root. Every endpoint declaring none carries the
          same list, so a reader could find it on any operation page and never
          learn that it is the API's default rather than that endpoint's own
          choice -- which is the difference between one endpoint to check and
          all of them. */}
      {api.secured_by && api.secured_by.length > 0 && (
        <Section title="Secured by default">
          <SecuredByList schemes={api.secured_by} index={index} />
          <p className="prose aside">Every endpoint that declares no security of its own requires this.</p>
        </Section>
      )}

      <Annotations applied={api.annotations} index={index} />

      {/* `{tenant}` in the base URI is a value every caller has to supply, so a
          reader who cannot see it cannot build a request at all (docs/16 § 11.4). */}
      <ParameterTable title="Base URI parameters" parameters={api.base_uri_parameters} index={index} />

      {/* Titles and links, not the prose. Each item has a page of its own now,
          and inlined here they were four collapsed rows nobody could link to
          below a table of counts. */}
      {api.documentation && api.documentation.length > 0 && (
        <Section title="Documentation">
          <ul className="usages">
            {api.documentation.map((item, at) => (
              <li key={at}>
                <Link to={`/documentation/${at}`}>{item.title}</Link>
              </li>
            ))}
          </ul>
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
                      <code>{oneLine(applied.value)}</code>
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

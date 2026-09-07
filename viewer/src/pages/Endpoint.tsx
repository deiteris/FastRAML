/**
 * A resource: what is true of every call to it, and the way in to its methods.
 *
 * Not the methods themselves. A resource with six of them rendered every one on
 * a single page, so the operation a reader came for was one of six full
 * schemas, and the URI parameters that apply to all of them scrolled away.
 */

import { Link } from 'react-router';
import { Annotations } from '../components/Annotations';
import { fromBaseUri } from '../components/Borrowed';
import { ParameterTable } from '../components/Parameters';
import { SecuredByList } from '../components/Security';
import { Url } from '../components/Url';
import { Empty, Prose, Section, Verb } from '../components/ui';
import { methodsOf } from '../model';
import { useParams } from 'react-router';
import type { Props } from './props';

export function EndpointPage({ document, index }: Props) {
  const { path } = useParams();
  const full = decodeURIComponent(path ?? '');
  const endpoint = document.endpoints[full];
  if (!endpoint) {
    return (
      <Empty>
        No resource at <code>{full}</code>. It may be a path segment with no methods of its own.
      </Empty>
    );
  }
  const methods = methodsOf(endpoint);
  return (
    <article>
      <h1>
        <Url api={document.entry_point} path={full} />
      </h1>
      {endpoint.display_name && <p className="subtitle">{endpoint.display_name}</p>}
      <Prose>{endpoint.description}</Prose>
      <SecuredByList schemes={endpoint.secured_by} index={index} />
      <Annotations applied={endpoint.annotations} index={index} />

      {/* Propagated down by P6, so a nested resource shows the ones it inherited
          as well as its own -- which is what a caller has to supply. The base
          URI's are joined to them for the same reason: `{tenant}` is not part
          of this path, and a caller cannot address the resource without it. */}
      <ParameterTable
        title="URI parameters"
        parameters={endpoint.uri_parameters}
        borrowed={fromBaseUri(document.entry_point?.base_uri_parameters)}
        index={index}
      />

      {methods.length > 0 ? (
        <Section title="Methods">
          <ul className="methods">
            {methods.map(([method, operation]) => (
              <li key={method}>
                <Link to={`/endpoints/${encodeURIComponent(full)}/${method}`} className="method-link">
                  <Verb method={method} />
                  <span className="method-name">{operation.display_name ?? operation.description ?? full}</span>
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      ) : (
        <Empty>This resource declares no methods.</Empty>
      )}
    </article>
  );
}

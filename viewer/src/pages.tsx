/**
 * One page per section of a document.
 *
 * The sections are the tree's own top-level keys, and the arrangement follows
 * what the three reference UIs converged on independently: an overview, the
 * endpoints nested by path, and the declarations reachable on their own pages
 * so a `$ref` has somewhere to land.
 */

import { Link, useParams } from 'react-router';
import { Annotations, ParameterTable, ShapeView } from './components/Shape';
import { Chip, Code, Disclosure, Empty, KeyValues, Prose, Section } from './components/ui';
import {
  type Document,
  type Index,
  type Operation,
  type Ref,
  type Response,
  type SecuredBy,
  type Shape,
  declarations,
  methodsOf,
  spelling,
} from './model';

interface Props {
  document: Document;
  index: Index;
}

/* -- overview ------------------------------------------------------------------ */

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
          ['Base URI', api.base_uri ? <code>{api.base_uri}</code> : null],
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

/* -- endpoints ----------------------------------------------------------------- */

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
  return (
    <article>
      <h1>
        <code>{full}</code>
      </h1>
      {endpoint.display_name && <p className="subtitle">{endpoint.display_name}</p>}
      <Prose>{endpoint.description}</Prose>
      <SecuredByList schemes={endpoint.secured_by} index={index} />
      <Annotations applied={endpoint.annotations} index={index} />

      {/* Propagated down by P6, so a nested resource shows the ones it inherited
          as well as its own -- which is what a caller has to supply. */}
      <ParameterTable title="URI parameters" parameters={endpoint.uri_parameters} index={index} />

      {methodsOf(endpoint).map(([method, operation]) => (
        <OperationView key={method} method={method} operation={operation} index={index} />
      ))}
      {Object.keys(endpoint.operations).length === 0 && <Empty>This resource declares no methods.</Empty>}
    </article>
  );
}

function OperationView({ method, operation, index }: { method: string; operation: Operation; index: Index }) {
  return (
    <section className="operation">
      <h2>
        <Chip tone="method">{method.toUpperCase()}</Chip>
        {operation.display_name && <span className="operation-name">{operation.display_name}</span>}
      </h2>
      <Prose>{operation.description}</Prose>
      <SecuredByList schemes={operation.secured_by} index={index} />
      <Annotations applied={operation.annotations} index={index} />

      <ParameterTable title="Headers" parameters={operation.headers} index={index} />
      <ParameterTable title="Query parameters" parameters={operation.query_parameters} index={index} />
      {operation.query_string && (
        <div className="parameters">
          <h4>Query string</h4>
          <ShapeView shape={operation.query_string} index={index} />
        </div>
      )}

      <Bodies title="Request body" bodies={operation.bodies} index={index} />

      {Object.entries(operation.responses).length > 0 && (
        <div className="responses">
          <h4>Responses</h4>
          {Object.entries(operation.responses)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([code, response]) => (
              <ResponseView key={code} code={code} response={response} index={index} />
            ))}
        </div>
      )}
    </section>
  );
}

function ResponseView({ code, response, index }: { code: string; response: Response; index: Index }) {
  return (
    <Disclosure
      open
      summary={
        <>
          <Chip tone="status">{code}</Chip>
          <span className="response-description">{response.description}</span>
        </>
      }
    >
      <Annotations applied={response.annotations} index={index} />
      <ParameterTable title="Headers" parameters={response.headers} index={index} />
      <Bodies title="Body" bodies={response.bodies} index={index} />
    </Disclosure>
  );
}

function Bodies({
  title,
  bodies,
  index,
}: {
  title: string;
  bodies?: Record<string, Shape | Ref | null>;
  index: Index;
}) {
  const entries = Object.entries(bodies ?? {});
  if (entries.length === 0) return null;
  return (
    <div className="bodies">
      <h4>{title}</h4>
      {entries.map(([media, shape]) => (
        <div key={media} className="body">
          <Chip tone="plain">{media}</Chip>
          <ShapeView shape={shape} index={index} hideType />
        </div>
      ))}
    </div>
  );
}

/**
 * A `securedBy:` list.
 *
 * `is_null` is `securedBy: [null]`, the way an author *removes* an inherited
 * scheme. It binds to a real definition of type `null`, so a view keeping only
 * names would render it as a scheme called "null" -- which is why the emitter
 * carries the flag and this reads it.
 */
function SecuredByList({ schemes, index }: { schemes?: SecuredBy[]; index: Index }) {
  if (!schemes || schemes.length === 0) return null;
  return (
    <div className="shape-line">
      <span className="label">secured by</span>
      {schemes.map((scheme, at) => {
        if (scheme.is_null) {
          return (
            <Chip key={at} tone="optional" title="securedBy: [null] -- this resource may be called unauthenticated">
              unsecured
            </Chip>
          );
        }
        const entry = index.get(scheme.declaration);
        return (
          <span key={at} className="secured">
            {entry ? (
              <Link to={entry.href} className="typelink">
                {scheme.name}
              </Link>
            ) : (
              <Chip tone={scheme.bound ? 'plain' : 'warn'}>{scheme.name}</Chip>
            )}
            {/* `null` is "not narrowed" and `[]` is "narrowed to nothing"; the
                two are different and the emitter keeps them apart. */}
            {scheme.scopes !== null && (
              <span className="scopes">
                {scheme.scopes.length === 0 ? (
                  <Chip tone="warn">no scopes</Chip>
                ) : (
                  scheme.scopes.map((scope) => <Chip key={scope}>{scope}</Chip>)
                )}
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}

/* -- declarations ---------------------------------------------------------------- */

export function TypePage({ document, index }: Props) {
  const { file, name } = useParams();
  const shape = document.types[decodeURIComponent(file ?? '')]?.[decodeURIComponent(name ?? '')];
  if (!shape) return <Empty>No type named {name} in {file}.</Empty>;
  return (
    <article>
      <h1>{shape.name ?? name}</h1>
      {/* The subtitle carries the type chip, so the shape below is rendered
          `hideType` -- rendering both printed `string | number` twice under a
          heading that had already said it. */}
      <p className="subtitle">
        <code>{file}</code> · <Chip tone="type">{spelling(shape)}</Chip>
        {/* The expression and the kind are different answers: `Prices` is
            written `Money[]` and is an array, and `Book` is written `Entity`
            and is an object. Showing only the first reads as though Book were
            Entity, which is what `extends` is for. */}
        {spelling(shape) !== shape.type && <Chip>{shape.type}</Chip>}
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

export function SecuritySchemePage({ document, index }: Props) {
  const { file, name } = useParams();
  const scheme = document.security_schemes[decodeURIComponent(file ?? '')]?.[decodeURIComponent(name ?? '')];
  if (!scheme) return <Empty>No security scheme named {name} in {file}.</Empty>;
  const described = scheme.described_by;
  return (
    <article>
      <h1>{scheme.display_name ?? scheme.name}</h1>
      <p className="subtitle">
        <code>{file}</code> · <Chip tone="type">{scheme.type}</Chip>
      </p>
      <Prose>{scheme.description}</Prose>
      <Annotations applied={scheme.annotations} index={index} />

      {/* The OAuth 2.0 URLs, grants and scopes. Without them a reader knows a
          scheme is required and nothing about how to satisfy it (§ 11.4). */}
      {scheme.settings && (
        <Section title="Settings">
          <KeyValues
            rows={Object.entries(scheme.settings).map(
              ([key, value]) =>
                [
                  key,
                  Array.isArray(value) ? (
                    value.map((item, at) => <Chip key={at}>{String(item)}</Chip>)
                  ) : (
                    <code>{String(value)}</code>
                  ),
                ] as [string, React.ReactNode],
            )}
          />
        </Section>
      )}

      {described && (
        <Section title="What a secured request carries">
          <ParameterTable title="Headers" parameters={described.headers} index={index} />
          <ParameterTable title="Query parameters" parameters={described.query_parameters} index={index} />
          {described.query_string && <ShapeView shape={described.query_string} index={index} />}
          {described.responses &&
            Object.entries(described.responses).map(([code, response]) => (
              <ResponseView key={code} code={code} response={response} index={index} />
            ))}
        </Section>
      )}

      <Usages document={document} address={scheme.id} />
    </article>
  );
}

/**
 * Where a declaration is used, found by scanning for its address.
 *
 * A tree carries containment and drops the reverse direction, so this is a scan
 * rather than a lookup -- the join the graph exists to make cheap (§ 4). At the
 * size a browser holds a document, a scan is the right trade; a consumer that
 * wants it indexed reads `pyraml refs` instead.
 */
function Usages({ document, address }: { document: Document; address: string | null }) {
  if (address === null) return null;
  const found: string[] = [];
  for (const [path, endpoint] of Object.entries(document.endpoints)) {
    if (JSON.stringify(endpoint).includes(JSON.stringify(address))) found.push(path);
  }
  if (found.length === 0) return null;
  return (
    <Section title={`Used by ${found.length} resource${found.length === 1 ? '' : 's'}`}>
      <ul className="usages">
        {found.map((path) => (
          <li key={path}>
            <Link to={`/endpoints/${encodeURIComponent(path)}`}>
              <code>{path}</code>
            </Link>
          </li>
        ))}
      </ul>
    </Section>
  );
}

/* -- listings -------------------------------------------------------------------- */

export function TypeList({ document, index }: Props) {
  return <DeclarationList document={document} index={index} of="types" title="Types" />;
}

export function AnnotationTypeList({ document, index }: Props) {
  return <DeclarationList document={document} index={index} of="annotation_types" title="Annotation types" />;
}

function DeclarationList({
  document,
  index,
  of,
  title,
}: Props & { of: 'types' | 'annotation_types'; title: string }) {
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
            <th>File</th>
          </tr>
        </thead>
        <tbody>
          {all.map(({ file, name, value }) => {
            const entry = index.get(value.id);
            return (
              <tr key={`${file}/${name}`}>
                <td className="property-name">
                  {entry ? <Link to={entry.href}>{name}</Link> : name}
                </td>
                <td>
                  <Chip tone="type">{spelling(value)}</Chip>
                </td>
                <td className="property-description">{value.description}</td>
                <td>
                  <code className="dim">{file}</code>
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
                <td className="property-description">{value.description}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </article>
  );
}

/**
 * `/n/:address` -- resolve any address to the page that shows it.
 *
 * The addresses are the same ones `pyraml graph` prints, so an address copied
 * out of a query result opens here. That join is what § 3.1 is for, and it costs
 * one route.
 */
export function ResolveAddress({ index }: Props) {
  const { address } = useParams();
  const entry = index.get(decodeURIComponent(address ?? ''));
  if (!entry) {
    return (
      <Empty>
        Nothing at <code>{address}</code>. Only declarations and resources have a page; a nested shape is shown where it
        sits.
      </Empty>
    );
  }
  return (
    <Empty>
      <Link to={entry.href}>Go to {entry.name}</Link>
    </Empty>
  );
}

export function NotFound() {
  return (
    <Empty>
      No such page. <Link to="/">Back to the overview.</Link>
    </Empty>
  );
}

export { Code };

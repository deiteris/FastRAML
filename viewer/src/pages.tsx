/**
 * One page per section of a document.
 *
 * The sections are the tree's own top-level keys, and the arrangement follows
 * what the three reference UIs converged on independently: an overview, the
 * endpoints nested by path, and the declarations reachable on their own pages
 * so a `$ref` has somewhere to land.
 */

import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { Annotations, From, ParameterTable, ShapeView, restates } from './components/Shape';
import { Chip, Code, Disclosure, Empty, KeyValues, Lock, Prose, Section, Tabs, Verb } from './components/ui';
import {
  type Document,
  type EntryPoint,
  type Index,
  type Ref,
  type Response,
  type SecuredBy,
  type SecurityScheme,
  type Shape,
  baseUriOf,
  declarations,
  humanise,
  methodsOf,
  schemeOf,
  spellingOf,
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
          // Resolved, with `{version}` filled in, because that is the string a
          // caller prefixes to every path -- and the one every endpoint page
          // now shows in front of its own.
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

/* -- endpoints ----------------------------------------------------------------- */

/**
 * The address a caller actually uses: the base URI, then the path.
 *
 * A path on its own is not something anyone can call. The two are one string
 * and are styled as one, with the base dimmed -- the path is what distinguishes
 * this page from every other, and the base is what makes it a URL.
 *
 * `scheme` is a method-level `protocols:` narrower than the base URI's, which
 * changes the address for that method alone.
 */
function Url({ api, path, protocols }: { api: EntryPoint | null; path: string; protocols?: string[] }) {
  const base = baseUriOf(api);
  const scheme = schemeOf(protocols, base);
  const shown = scheme ? base.replace(/^[a-z][a-z0-9+.-]*:/i, `${scheme}:`) : base;
  return (
    <code className="url">
      <span className="url-base">{shown}</span>
      {path}
    </code>
  );
}

/**
 * A resource: what is true of every call to it, and the way in to its methods.
 *
 * Not the methods themselves. A resource with six of them rendered every one on
 * a single page, so the operation a reader came for was one of six full
 * schemas, and the URI parameters that apply to all of them scrolled away.
 */
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
          as well as its own -- which is what a caller has to supply. */}
      <ParameterTable title="URI parameters" parameters={endpoint.uri_parameters} index={index} />

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

/** One method, which is the unit a reader actually came for. */
export function OperationPage({ document, index }: Props) {
  const { path, method } = useParams();
  const [chosen, setChosen] = useState(0);
  const full = decodeURIComponent(path ?? '');
  const endpoint = document.endpoints[full];
  const operation = endpoint?.operations[method ?? ''];
  if (!endpoint || !operation) {
    return (
      <Empty>
        No <code>{method?.toUpperCase()}</code> on <code>{full}</code>.
      </Empty>
    );
  }

  // P5 resolves `securedBy` inheritance, so an operation list is already the
  // effective one. Falling back to the resource would re-display a requirement
  // the author had deliberately removed.
  const schemes = operation.secured_by ?? [];
  const at = Math.min(chosen, Math.max(schemes.length - 1, 0));
  const scheme = schemes[at];
  const byId = Object.fromEntries(declarations(document.security_schemes).map(({ value }) => [value.id, value]));
  const active = scheme && !scheme.is_null ? byId[scheme.declaration ?? ''] : undefined;
  const adds = active?.described_by;
  const optional = schemes.some((one) => one.is_null);

  return (
    <article>
      <h1 className="operation-title">
        <Verb method={method ?? ''} large />
        <Url api={document.entry_point} path={full} protocols={operation.protocols} />
        {schemes.length > 0 && (
          <Lock open={optional} title={optional ? 'may be called unauthenticated' : 'requires authentication'} />
        )}
      </h1>
      {operation.display_name && <p className="subtitle">{operation.display_name}</p>}
      {/* A method may narrow the API's protocols. The URL above already shows
          the scheme; this says it was this method's own decision. */}
      {operation.protocols && operation.protocols.length > 0 && (
        <div className="shape-line">
          <span className="label">protocols</span>
          {operation.protocols.map((protocol) => (
            <Chip key={protocol}>{protocol}</Chip>
          ))}
        </div>
      )}
      <Prose>{operation.description}</Prose>
      <Annotations applied={operation.annotations} index={index} />

      <SecurityChoice
        schemes={schemes}
        declared={active}
        chosen={at}
        onChoose={setChosen}
        index={index}
      />

      <ParameterTable title="URI parameters" parameters={endpoint.uri_parameters} index={index} />
      <ParameterTable
        title="Headers"
        parameters={operation.headers}
        added={adds?.headers}
        from={active?.name}
        index={index}
      />
      <ParameterTable
        title="Query parameters"
        parameters={operation.query_parameters}
        added={adds?.query_parameters}
        from={active?.name}
        index={index}
      />
      {operation.query_string && (
        <section className="parameters">
          <h4>Query string</h4>
          <ShapeView shape={operation.query_string} index={index} />
        </section>
      )}

      <Bodies title="Request body" bodies={operation.bodies} index={index} />
      <Responses responses={operation.responses} added={adds?.responses} from={active?.name} index={index} />
    </article>
  );
}

/**
 * Responses, as tabs.
 *
 * A status code is a whole alternative outcome, and a list of collapsed rows
 * makes a reader open them one at a time to find the one they want. The codes
 * are all visible at once here, and exactly one body is on screen.
 */
function Responses({
  responses,
  added,
  from,
  index,
  title = 'Responses',
}: {
  responses: Record<string, Response>;
  /** What the chosen security scheme adds -- a `401`, typically. */
  added?: Record<string, Response>;
  from?: string;
  index: Index;
  title?: string;
}) {
  const own = new Set(Object.keys(responses));
  const codes = Object.entries({ ...responses, ...added }).sort(([a], [b]) => a.localeCompare(b));
  if (codes.length === 0) return null;
  return (
    <section className="responses">
      <h4>{title}</h4>
      <Tabs
        items={codes.map(([code, response]) => ({
          key: code,
          label: code,
          // The first digit is the class, which is what the dot colours.
          tone: code[0] ?? 'plain',
          body: (
            <>
              {!own.has(code) && from && <From scheme={from} />}
              <Prose>{response.description}</Prose>
              <Annotations applied={response.annotations} index={index} />
              <ParameterTable title="Headers" parameters={response.headers} index={index} />
              <Bodies title="Body" bodies={response.bodies} index={index} />
              {!response.description && !response.bodies && !response.headers && <Empty>No content.</Empty>}
            </>
          ),
        }))}
      />
    </section>
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
          {/* The type is shown, because the chip beside it is the *media* type
              and says nothing about the shape. Hidden, a body of `Publication[]`
              read as a bare `each item Publication` -- the one word saying it
              was a list was the one word suppressed. */}
          <ShapeView shape={shape} index={index} />
        </div>
      ))}
    </div>
  );
}

/**
 * The security selector: one line, above the parameters it changes.
 *
 * `securedBy` is a **disjunction** -- a caller satisfies any one entry, not all
 * of them -- so this is a choice, not a list. `is_null` is `securedBy: [null]`,
 * how an author says a resource may also be called unauthenticated (docs/09
 * A3); it binds to a real definition of type `null`, so a view keeping only
 * names would render it as a scheme called "null".
 *
 * Choosing changes the page. A scheme is not only a gate: its `describedBy`
 * declares headers, query parameters and responses the operation *gains* when
 * secured that way, and those are merged into the operation own tables below,
 * each marked with the scheme it came from. Shown in a section of their own
 * they pushed the operation own parameters below the fold and made the reader
 * assemble the request from two places.
 */
function SecurityChoice({
  schemes,
  declared,
  chosen,
  onChoose,
  index,
}: {
  schemes: SecuredBy[];
  declared?: SecurityScheme;
  chosen: number;
  onChoose: (at: number) => void;
  index: Index;
}) {
  if (schemes.length === 0) return null;
  const scheme = schemes[chosen];
  const entry = index.get(scheme?.declaration);
  return (
    <div className="security-choice">
      {/* The choice on its own row, the consequences under it. Trailing the
          type, a link and the scopes onto the same line wrapped badly the
          moment there were three schemes or more than two scopes, which is the
          ordinary case rather than the exceptional one. */}
      <div className="security-pick">
        <Lock open={schemes.some((one) => one.is_null)} />
        <span className="label">secured by</span>
        {schemes.map((one, at) => (
          <button
            key={at}
            type="button"
            className={`scheme ${at === chosen ? 'is-chosen' : ''}`}
            onClick={() => onChoose(at)}
          >
            {one.is_null ? 'None' : one.name}
          </button>
        ))}
      </div>

      <div className="security-detail">
        {scheme?.is_null ? (
          <p className="prose">
            This operation may be called unauthenticated. Everything below is what it requires without a token.
          </p>
        ) : (
          <>
            <div className="shape-line">
              {declared && <Chip tone="type">{declared.type}</Chip>}
              {entry && (
                <Link to={entry.href} className="typelink">
                  {entry.name}
                </Link>
              )}
            </div>
            {declared?.description && <p className="prose">{declared.description}</p>}
            {/* `null` is "not narrowed" and `[]` is "narrowed to nothing"; the
                two are different and the emitter keeps them apart. */}
            {scheme && scheme.scopes !== null && (
              <div className="shape-line">
                <span className="label">scopes</span>
                {scheme.scopes.length === 0 ? (
                  <Chip tone="warn">narrowed to none</Chip>
                ) : (
                  scheme.scopes.map((scope) => <Chip key={scope}>{scope}</Chip>)
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** The one-line form, for a resource. */
function SecuredByList({ schemes, index }: { schemes?: SecuredBy[]; index: Index }) {
  if (!schemes || schemes.length === 0) return null;
  return (
    <div className="shape-line">
      <Lock open={schemes.some((scheme) => scheme.is_null)} title="secured" />
      <span className="label">secured by</span>
      {schemes.map((scheme, at) => (
        <span key={at} className="secured">
          {at > 0 && <span className="or">or</span>}
          {scheme.is_null ? (
            <Chip tone="optional">unauthenticated</Chip>
          ) : (
            <SchemeName scheme={scheme} index={index} />
          )}
        </span>
      ))}
    </div>
  );
}

function SchemeName({ scheme, index }: { scheme: SecuredBy; index: Index }) {
  const entry = index.get(scheme.declaration);
  if (!entry) return <Chip tone={scheme.bound ? 'plain' : 'warn'}>{scheme.name}</Chip>;
  return (
    <Link to={entry.href} className="typelink">
      {entry.name}
    </Link>
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
      {/* The kind, and the expression only where it says something the
          `extends` line below does not. `Book` is written `type: Entity`, so
          the subtitle read `Entity · object` above a line reading
          `extends Entity` -- the same fact twice, once without the link. */}
      <p className="subtitle">
        <code>{file}</code> · <Chip tone="type">{shape.type}</Chip>
        {restates(shape, index) || <Chip>{spellingOf(shape, index)}</Chip>}
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
                  humanise(key),
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
          {described.responses && <Responses responses={described.responses} index={index} />}
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
                  <Chip tone="type">{spellingOf(value, index)}</Chip>
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

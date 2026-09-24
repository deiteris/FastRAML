/** One method, which is the unit a reader actually came for. */

import { useState } from 'react';
import { useParams } from 'react-router';
import { Annotations } from '../components/Extra';
import { Bodies } from '../components/Bodies';
import { fromBaseUri, fromScheme } from '../components/Borrowed';
import { ParameterTable, QueryString } from '../components/Parameters';
import { Responses } from '../components/Responses';
import { SecurityChoice } from '../components/Security';
import { Url } from '../components/Url';
import { Prose } from '../components/markdown';
import { Boundary, Chip, CopyButton, Empty, Lock, Verb } from '../components/ui';
import { baseFor, isHttpMethod } from '../model';
import type { Props } from './props';

export function OperationPage({ document, index }: Props) {
  const { path, method } = useParams();
  const [chosen, setChosen] = useState(0);
  const full = decodeURIComponent(path ?? '');
  const endpoint = document.endpoints[full];
  const operation = endpoint && method && isHttpMethod(method) ? endpoint.operations[method] : undefined;
  if (!endpoint || !method || !operation) {
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
  const active = scheme && !scheme.is_null ? index.scheme(scheme.declaration) : undefined;
  const adds = active?.described_by;
  const optional = schemes.some((one) => one.is_null);

  return (
    <article>
      {/* The copy button beside the heading, not in it: inside, "Copy URL"
          became part of the heading's accessible name. */}
      <div className="operation-head">
        <h1 className="operation-title">
          <Verb method={method} large />
          <Url api={document.entry_point} path={full} protocols={operation.protocols} />
          {schemes.length > 0 && (
            <Lock open={optional} title={optional ? 'may be called unauthenticated' : 'requires authentication'} />
          )}
        </h1>
        <CopyButton text={`${baseFor(document.entry_point, operation.protocols)}${full}`} label="Copy URL" />
      </div>
      {operation.display_name && <h2 className="display-name">{operation.display_name}</h2>}
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

      <Boundary what="request">
        <SecurityChoice schemes={schemes} declared={active} chosen={at} onChoose={setChosen} index={index} />

        {/* The base URI's parameters belong here: `{tenant}` is not part of this
            path and is not optional, so a caller reading only the path cannot
            build the request. */}
        <ParameterTable
          title="URI parameters"
          anchor="uri-parameters"
          parameters={endpoint.uri_parameters}
          borrowed={fromBaseUri(document.entry_point?.base_uri_parameters)}
          index={index}
        />
        <ParameterTable
          title="Headers"
          anchor="headers"
          parameters={operation.headers}
          borrowed={fromScheme(adds?.headers, active?.name)}
          index={index}
        />
        <ParameterTable
          title="Query parameters"
          anchor="query-parameters"
          parameters={operation.query_parameters}
          borrowed={fromScheme(adds?.query_parameters, active?.name)}
          index={index}
        />
        <QueryString shape={operation.query_string} index={index} anchor="query-string" />

        <Bodies title="Request body" anchor="request-body" bodies={operation.bodies} index={index} />
      </Boundary>
      <Boundary what="responses">
        <Responses
          responses={operation.responses}
          borrowed={fromScheme(adds?.responses, active?.name)}
          index={index}
          anchor="responses"
        />
      </Boundary>
    </article>
  );
}

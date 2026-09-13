/** One security scheme: what it is, how to satisfy it, what it adds. */

import { useParams } from 'react-router';
import { Annotations } from '../components/Extra';
import { ParameterTable } from '../components/Parameters';
import { Responses } from '../components/Responses';
import { ShapeView } from '../components/Shape';
import { Usages } from '../components/Usages';
import { Prose } from '../components/markdown';
import { oneLine } from '../components/json';
import { Chip, Empty, KeyValues, Section } from '../components/ui';
import { type Json, humanise } from '../model';
import type { Props } from './props';

export function SecuritySchemePage({ document, index }: Props) {
  const { file, name } = useParams();
  const scheme = document.security_schemes[decodeURIComponent(file ?? '')]?.[decodeURIComponent(name ?? '')];
  if (!scheme) return <Empty>No security scheme named {name}.</Empty>;
  const described = scheme.described_by;
  return (
    <article>
      <h1>{scheme.display_name ?? scheme.name}</h1>
      <p className="subtitle">
        <Chip tone="type">{scheme.type}</Chip>
      </p>
      <Prose>{scheme.description}</Prose>
      <Annotations applied={scheme.annotations} index={index} />

      {/* The OAuth 2.0 URLs, grants and scopes. Without them a reader knows a
          scheme is required and nothing about how to satisfy it (§ 11.4). */}
      {scheme.settings && (
        <Section title="Settings">
          <KeyValues
            rows={Object.entries(scheme.settings).map(
              ([key, value]) => [humanise(key), <Setting key={key} value={value} />] as [string, React.ReactNode],
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
 * One setting's value.
 *
 * A scheme's settings are open-ended -- the tree types them `Json` and means it
 * -- so this handles the three shapes one can take rather than a table of the
 * keys OAuth 2.0 happens to use. A list becomes chips, because `scopes` and
 * `authorizationGrants` are both lists a reader scans.
 */
function Setting({ value }: { value: Json }) {
  if (Array.isArray(value)) {
    return (
      <>
        {value.map((item, at) => (
          <Chip key={at}>{oneLine(item)}</Chip>
        ))}
      </>
    );
  }
  return <code>{oneLine(value)}</code>;
}

/**
 * Responses, as tabs.
 *
 * A status code is a whole alternative outcome, and a list of collapsed rows
 * makes a reader open them one at a time to find the one they want. The codes
 * are all visible at once here, and exactly one body is on screen.
 */

import type { Index, Response } from '../model';
import { Annotations } from './Annotations';
import { Bodies } from './Bodies';
import { ParameterTable } from './Parameters';
import { From } from './Shape';
import { Empty, Prose, Tabs } from './ui';

export function Responses({
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

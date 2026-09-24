/**
 * Responses, as tabs.
 *
 * A status code is a whole alternative outcome, and a list of collapsed rows
 * makes a reader open them one at a time to find the one they want. The codes
 * are all visible at once here, and exactly one body is on screen.
 */

import type { Index, Response } from '../model';
import { Annotations } from './Extra';
import { Bodies } from './Bodies';
import { type Borrowed, From } from './Borrowed';
import { ParameterTable } from './Parameters';
import { Prose } from './markdown';
import { Empty, Tabs } from './ui';

/**
 * Every status an operation can answer with, the chosen scheme's included.
 *
 * The operation's own response wins over a scheme's for the same status, as a
 * declared parameter wins over a borrowed one: the operation said something
 * about its `401` that the scheme's general one does not.
 */
export function responsesOf(responses: Record<string, Response>, borrowed?: Borrowed<Response>): [string, Response][] {
  return Object.entries({ ...borrowed?.rows, ...responses }).sort(([a], [b]) => a.localeCompare(b));
}

export function Responses({
  responses,
  borrowed,
  index,
  title = 'Responses',
}: {
  responses: Record<string, Response>;
  /** What the chosen security scheme adds -- a `401`, typically. */
  borrowed?: Borrowed<Response>;
  index: Index;
  title?: string;
}) {
  const own = new Set(Object.keys(responses));
  const codes = responsesOf(responses, borrowed);
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
              {!own.has(code) && borrowed && (
                <From label={borrowed.label} title={borrowed.title} secured={borrowed.secured} />
              )}
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

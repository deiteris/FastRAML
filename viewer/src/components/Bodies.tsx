/**
 * A body, which is one shape *per media type*.
 *
 * RAML lets a request or a response declare a body under several media types,
 * and a document with `mediaType: [application/json, application/xml]` gets
 * every body under both without writing either. Stacked, that is the same
 * schema printed twice with a chip between the copies, and a reader has to
 * compare them to find out they are the same -- which is the case the tabs are
 * for. One media type keeps its chip and no strip, for the same reason a single
 * example does not become a tab.
 */

import type { Index, Ref, Shape } from '../model';
import { ShapeView } from './Shape';
import { Chip, Tabs } from './ui';

export function Bodies({
  title,
  bodies,
  index,
}: {
  title: string;
  bodies?: Record<string, Shape | Ref | null>;
  index: Index;
}) {
  const entries = Object.entries(bodies ?? {});
  const [only] = entries;
  if (entries.length === 0) return null;
  return (
    <div className="bodies">
      <h4>{title}</h4>
      {entries.length === 1 && only ? (
        <div className="body">
          <Chip tone="plain">{only[0]}</Chip>
          {/* The type is shown, because the chip beside it is the *media* type
              and says nothing about the shape. Hidden, a body of
              `Publication[]` read as a bare `each item Publication` -- the one
              word saying it was a list was the one word suppressed. */}
          <ShapeView shape={only[1]} index={index} />
        </div>
      ) : (
        <Tabs
          label="media type"
          items={entries.map(([media, shape]) => ({
            key: media,
            label: media,
            body: (
              <div className="body">
                <ShapeView shape={shape} index={index} />
              </div>
            ),
          }))}
        />
      )}
    </div>
  );
}

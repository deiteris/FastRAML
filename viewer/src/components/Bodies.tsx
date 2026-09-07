/**
 * A body, which is one shape *per media type*.
 *
 * RAML lets a request or a response declare a body under several media types,
 * and a document with `mediaType: [application/json, application/xml]` gets
 * every body under both without writing either. Stacked, that is the same
 * schema printed twice with a chip between the copies, and a reader has to
 * compare them to find out they are the same.
 *
 * A single media type is a strip of one, not a chip. The two forms were a chip
 * and a tab, in the same place, meaning the same thing, and which one appeared
 * depended on how many media types the *document* declared -- so a reader
 * moving between two operations saw the media type move and change shape for a
 * reason that has nothing to do with either operation.
 */

import type { Index, Ref, Shape } from '../model';
import { ShapeView } from './Shape';
import { Tabs } from './ui';

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
  if (entries.length === 0) return null;
  return (
    <div className="bodies">
      <h4>{title}</h4>
      <Tabs
        label="media type"
        items={entries.map(([media, shape]) => ({
          key: media,
          label: media,
          body: (
            <div className="body">
              {/* The type is shown, because the tab beside it is the *media*
                  type and says nothing about the shape. Hidden, a body of
                  `Publication[]` read as a bare `each item Publication` -- the
                  one word saying it was a list was the one word suppressed. */}
              <ShapeView shape={shape} index={index} />
            </div>
          ),
        }))}
      />
    </div>
  );
}

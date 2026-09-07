/**
 * The type renderer -- the one component the rest of the app is arranged around.
 *
 * It implements the traversal law directly (docs/16 § 11.7): descend
 * containment, follow a link only when asked, stop at a recursion marker. There
 * is no ancestor set and no depth budget anywhere below, because the emitter
 * guarantees a cycle is always *marked* -- an unmarked one would hang this, and
 * that is the property `tests/unit/test_consumer_traversal.py` pins.
 *
 * The arrangement is an attribute list, not a table. A type is a tree and a
 * table can only grow one way: nesting went into the Type column, so a nested
 * object pushed its children rightward into a narrower and narrower strip while
 * the page's whole right half stayed empty. Children belong *under* the
 * attribute they belong to, indented by a rule.
 */

import { useState } from 'react';
import { Link } from 'react-router';
import {
  type Applied,
  type Index,
  type Parameter,
  type PatternProperty,
  type Property,
  type Ref,
  type Shape,
  facetsOf,
  isRecursive,
  isRef,
  labelOf,
  spelling,
} from '../model';
import { Chip, Code, Prose, Tabs } from './ui';

interface Props {
  shape: Shape | Ref | null | undefined;
  index: Index;
  /**
   * What the surrounding context already displays, so this does not repeat it.
   *
   * Two independent flags rather than one: an attribute row shows the type on
   * its own head line and has room for prose beneath, while a declaration page
   * puts the type in the heading and wants the prose.
   */
  hideType?: boolean;
  hideDescription?: boolean;
  /**
   * The caller's own line already named the item type -- `tags array of
   * string` -- so the nested block would repeat it.
   *
   * A property of the *caller*, not of the shape: a union panel hides the type
   * line, so a `Body` deciding this for itself left the `array` member of
   * `Book[] | Review` rendering nothing at all.
   */
  hideItems?: boolean;
  /**
   * This shape's `type_expr` belongs to its container, so only its `type` is
   * its own. True of a union member and an inlined supertype -- see `spelling`.
   */
  borrowed?: boolean;
}

export function ShapeView({ shape, index, hideType, hideDescription, hideItems, borrowed }: Props) {
  if (shape === null || shape === undefined) return <Chip tone="type">any</Chip>;
  if (isRef(shape)) return <RefView node={shape} index={index} />;
  if (isRecursive(shape)) return <RecursionView node={shape} index={index} />;
  return (
    <Body
      shape={shape}
      index={index}
      hideType={hideType}
      hideDescription={hideDescription}
      hideItems={hideItems}
      borrowed={borrowed}
    />
  );
}

/**
 * A link: a name that navigates, and a control that expands it in place.
 *
 * Both, because they answer different questions -- navigating loses your place
 * in a response body, expanding keeps it but buries the declaration's own page.
 * They are two visibly different controls for that reason. A caret glyph beside
 * a link read as decoration *on* the link, so the two flows were one ambiguous
 * one; the expander is now a labelled button that says what it will do.
 *
 * Neither expands on render. That is the loop.
 */
function RefView({ node, index }: { node: Ref; index: Index }) {
  const [open, setOpen] = useState(false);
  const entry = index.get(node.$ref);
  const target = index.shape(node.$ref);
  if (!entry) {
    return (
      <Chip tone="warn" title={node.$ref}>
        unresolved
      </Chip>
    );
  }
  return (
    <span className="reference">
      <Link to={entry.href} className="typelink">
        {entry.name}
      </Link>
      {target?.description && <span className="reference-desc">{target.description}</span>}
      {expandable(target) && (
        <>
          <button type="button" className="expander" aria-expanded={open} onClick={() => setOpen(!open)}>
            <span className="expander-sign">{open ? '−' : '+'}</span>
            {open ? 'Hide child attributes' : 'Show child attributes'}
          </button>
          {open && (
            <div className="nested">
              <Body shape={target} index={index} hideType hideDescription />
            </div>
          )}
        </>
      )}
    </span>
  );
}

/**
 * The marker that says the structure repeats.
 *
 * A stop, never something expandable: `head` points at what the walk is already
 * inside, so an expander here is the loop by another name. The name still
 * links, because navigating to a declaration is finite.
 */
function RecursionView({ node, index }: { node: Shape & { head: Ref }; index: Index }) {
  const entry = index.get(node.head.$ref);
  return (
    <span className="reference">
      <Chip tone="recursive" title="the structure repeats from here; it is not expanded">
        recursive
      </Chip>
      {entry ? (
        <Link to={entry.href} className="typelink">
          {entry.name}
        </Link>
      ) : (
        <span className="typelink">{node.name ?? index.label(node.head.$ref)}</span>
      )}
    </span>
  );
}

/**
 * Whether an array's items are already said by its own type line.
 *
 * `tags array of string` and `related Book[]` need no nested block: it was a
 * label and a rule around one word, and for a `[]` expression it repeated what
 * the reader had just read. An item with structure of its own -- an inline
 * object, a union -- still gets one.
 */
function simpleItems(shape: Shape): boolean {
  const items = shape.items;
  if (items === null || items === undefined || isRef(items) || isRecursive(items)) return true;
  return !items.properties && !items.any_of && !items.pattern_properties && !items.items;
}

/**
 * Whether a reference is worth an expander.
 *
 * "Show child attributes" has to have child attributes. An array does not --
 * it has an item type -- so `priceHistory: Prices` offered a control that
 * opened onto `each item -> Money`, one more click to reach what the line could
 * have said. A scalar has none either.
 *
 * The item's own expander is a different question and stays: `Money` has
 * attributes, and reaching them is the point of opening anything.
 */
function expandable(target: Shape | undefined): target is Shape {
  return Boolean(target && (target.properties || target.pattern_properties));
}

/** An array's item type, where it is worth naming on one line. */
function itemSummary(shape: Shape, index: Index): string | null {
  const items = shape.items;
  if (items === null || items === undefined) return null;
  if (isRef(items)) return index.label(items.$ref);
  if (isRecursive(items)) return items.name ?? 'recursive';
  return items.properties || items.any_of ? null : spelling(items, true);
}

/**
 * An array's type as one phrase: `array of string`, not `array` with a nested
 * block naming `string`. Left alone when the expression already says it --
 * `Book[]` is not improved by `Book[] of Book`.
 */
function arrayLine(shape: Shape, index: Index): string {
  const written = spelling(shape);
  if (shape.type !== 'array' || written.endsWith('[]')) return written;
  const item = itemSummary(shape, index);
  return item ? `${written} of ${item}` : written;
}

function Body({
  shape,
  index,
  hideType,
  hideDescription,
  hideItems,
  borrowed,
}: {
  shape: Shape;
  index: Index;
  hideType?: boolean;
  hideDescription?: boolean;
  hideItems?: boolean;
  borrowed?: boolean;
}) {
  const facets = facetsOf(shape);
  const inherits = shape.inherits ?? [];
  const properties = Object.entries(shape.properties ?? {});
  const patterns = Object.entries(shape.pattern_properties ?? {});
  const members = shape.any_of ?? [];
  // `hideType` means the container names this shape, so its `displayName`
  // belongs up there too. Rendered here it was a bare word between the
  // description and the facets, with nothing saying what it was.
  const named = !hideType && shape.display_name && shape.display_name !== shape.name;

  return (
    <div className="shape">
      {(!hideType || named) && (
        <div className="shape-head">
          {named && <span className="shape-display">{shape.display_name}</span>}
          {!hideType && <TypeChip shape={shape} borrowed={borrowed} />}
        </div>
      )}
      {!hideDescription && <Prose>{shape.description}</Prose>}

      {(facets.length > 0 || shape.enum) && (
        <div className="facets">
          {facets.map(([name, value]) => (
            <Chip key={name}>
              <span className="facet-name">{name}</span>
              <span className="facet-value">{render(value)}</span>
            </Chip>
          ))}
          {shape.enum?.map((value, at) => (
            <Chip key={`enum-${at}`} tone="enum">
              {render(value)}
            </Chip>
          ))}
        </div>
      )}

      {shape.allowed_targets && <Tagged label="allowedTargets" values={shape.allowed_targets} />}
      {shape.declares_facets && <Tagged label="declares facets" values={shape.declares_facets} />}
      {shape.custom_facets && Object.keys(shape.custom_facets).length > 0 && (
        <div className="shape-line">
          <span className="label">facets</span>
          {Object.entries(shape.custom_facets).map(([name, value]) => (
            <Chip key={name}>
              <span className="facet-name">{name}</span>
              <span className="facet-value">{render(value)}</span>
            </Chip>
          ))}
        </div>
      )}

      <Annotations applied={shape.annotations} index={index} />

      {/* Above the attributes, not below. An example is the fastest way to
          understand a type, and last it read as belonging to whichever
          attribute happened to come final. */}
      {shape.default !== undefined && <Labelled label="default" value={shape.default} />}
      {shape.example !== undefined && <Labelled label="example" value={shape.example} />}
      {shape.examples &&
        Object.entries(shape.examples).map(([name, value]) => (
          <Labelled key={name} label={`example: ${name}`} value={value} />
        ))}

      {inherits.length > 0 && (
        <div className="shape-line">
          <span className="label">extends</span>
          {inherits.map((parent, at) => (
            <RefLink key={at} parent={parent} index={index} />
          ))}
        </div>
      )}

      {members.length > 0 && <Union members={members} index={index} />}

      {shape.items !== undefined && !hideItems && (
        <Group label="each item">
          <ShapeView shape={shape.items} index={index} />
        </Group>
      )}

      {(properties.length > 0 || patterns.length > 0) && (
        <div className="attributes">
          {properties.map(([name, property]) => (
            <Attribute key={name} name={name} property={property} index={index} />
          ))}
          {patterns.map(([pattern, property]) => (
            <Attribute key={pattern} name={`/${property.pattern}/`} property={property} index={index} pattern />
          ))}
        </div>
      )}

      {shape.xml !== undefined && <Labelled label="xml" value={shape.xml} />}
    </div>
  );
}

/**
 * One named thing -- a property, a pattern property, a parameter, a header.
 *
 * Name, type and whether it is required on one line; prose under it; anything
 * nested under that again. Everything a reader scans for sits in one column,
 * which is what a table put in three.
 */
function Attribute({
  name,
  property,
  index,
  pattern,
}: {
  name: string;
  property: Property | PatternProperty | Parameter;
  index: Index;
  pattern?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const required = 'required' in property ? property.required : false;
  const shape = property.type;

  // The head line is name, type, flag -- in that order, always. Letting a
  // reference render itself here put its expander between the type and
  // `Required`, so the one word a reader scans for moved depending on whether
  // the type happened to be a link.
  const link = shape !== null && shape !== undefined && isRef(shape) ? index.get(shape.$ref) : undefined;
  const target = shape !== null && shape !== undefined && isRef(shape) ? index.shape(shape.$ref) : undefined;
  // What the removed expander would have led to, said on the line instead.
  const holds = target && target.type === 'array' ? arrayLine(target, index) : null;
  const inline = shape !== null && shape !== undefined && !isRef(shape) && !isRecursive(shape);
  const described = inline ? shape.description : target?.description;

  return (
    <div className="attr">
      <div className="attr-head">
        <code className="attr-name">{name}</code>
        {inline && shape.display_name && shape.display_name !== shape.name && (
          <span className="attr-display">{shape.display_name}</span>
        )}
        {inline ? (
          <span className="attr-type">{arrayLine(shape, index)}</span>
        ) : link ? (
          <>
            <Link to={link.href} className="typelink">
              {link.name}
            </Link>
            {holds && <span className="attr-type">{holds}</span>}
          </>
        ) : (
          <ShapeView shape={shape} index={index} />
        )}
        {pattern ? (
          <span className="attr-flag">pattern</span>
        ) : (
          required && <span className="attr-flag is-required">Required</span>
        )}
      </div>
      {/* A description is not an attribute, so it does not live behind the
          control that expands them. Where the type is a reference the prose
          belongs to the target, and reading it used to require opening the
          attribute list first. The expanded body suppresses it, so it appears
          once either way. */}
      {described && <p className="attr-desc">{described}</p>}
      {expandable(target) && (
        <>
          <button type="button" className="expander" aria-expanded={open} onClick={() => setOpen(!open)}>
            <span className="expander-sign">{open ? '−' : '+'}</span>
            {open ? 'Hide child attributes' : 'Show child attributes'}
          </button>
          {open && (
            <div className="nested">
              <Body shape={target} index={index} hideType hideDescription />
            </div>
          )}
        </>
      )}
      {inline && <Body shape={shape} index={index} hideType hideDescription hideItems={simpleItems(shape)} />}
    </div>
  );
}

/** The type name a reader recognises: the expression as written, else the kind. */
export function TypeChip({ shape, borrowed }: { shape: Shape; borrowed?: boolean }) {
  const written = spelling(shape, borrowed);
  return (
    <Chip tone="type" title={written === shape.type ? undefined : `a ${shape.type}`}>
      {written}
    </Chip>
  );
}

/**
 * A union, as a selector over its members.
 *
 * **`anyOf`, not "one of".** That is the model's field name, which this view
 * uses throughout (docs/16 § 11.9), and the two do not mean the same thing: a
 * value satisfying more than one member is still valid, which "one of" denies.
 *
 * A selector rather than a stack because a union member is a whole type. Two
 * object members rendered in sequence produce two attribute lists with nothing
 * between them saying where the first ended.
 */
function Union({ members, index }: { members: (Shape | Ref)[]; index: Index }) {
  return (
    <Tabs
      label="anyOf"
      items={members.map((member, at) => ({
        key: String(at),
        label: labelOf(member, index),
        body: <ShapeView shape={member} index={index} hideType borrowed />,
      }))}
    />
  );
}

/**
 * A reference as a name alone, with no expander.
 *
 * Used where an expander would be noise rather than a way in:
 *
 * - a **supertype**, because the projection is unwrapped and every attribute it
 *   contributes is already in the list below. `Book extends Entity` lists `id`
 *   and `createdAt` among its own, so expanding printed them a second time a
 *   few pixels from the first.
 * - anything with **no attribute list** -- see `expandable`.
 *
 * The link still matters in both: the target has a page, with its own prose and
 * examples and the list of what else points at it.
 *
 * An anonymous shape is not a link and not a declaration -- the `integer` in
 * `type: integer | number` after P9 distributes a facet exists nowhere else --
 * so it is rendered where it sits.
 */
function RefLink({ parent, index }: { parent: Shape | Ref; index: Index }) {
  if (!isRef(parent)) return <ShapeView shape={parent} index={index} borrowed />;
  const entry = index.get(parent.$ref);
  if (!entry) {
    return (
      <Chip tone="warn" title={parent.$ref}>
        unresolved
      </Chip>
    );
  }
  return (
    <Link to={entry.href} className="typelink">
      {entry.name}
    </Link>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="group">
      <span className="label">{label}</span>
      <div className="nested">{children}</div>
    </div>
  );
}

function Tagged({ label, values }: { label: string; values: string[] }) {
  return (
    <div className="shape-line">
      <span className="label">{label}</span>
      {values.map((value) => (
        <Chip key={value}>{value}</Chip>
      ))}
    </div>
  );
}

export function Annotations({ applied, index }: { applied?: Applied[]; index: Index }) {
  if (!applied || applied.length === 0) return null;
  return (
    <div className="shape-line">
      {applied.map((one, at) => {
        const entry = index.get(one.type);
        return (
          <Chip key={at} tone="warn">
            {entry ? (
              <Link to={entry.href} className="typelink">
                ({one.name})
              </Link>
            ) : (
              <span>({one.name})</span>
            )}
            {one.value !== null && one.value !== undefined && <span className="facet-value">{render(one.value)}</span>}
          </Chip>
        );
      })}
    </div>
  );
}

export function ParameterTable({
  title,
  parameters,
  index,
}: {
  title: string;
  parameters?: Record<string, Parameter>;
  index: Index;
}) {
  const rows = Object.entries(parameters ?? {});
  if (rows.length === 0) return null;
  return (
    <section className="parameters">
      <h4>{title}</h4>
      <div className="attributes">
        {rows.map(([name, parameter]) => (
          <Attribute key={name} name={name} property={parameter} index={index} />
        ))}
      </div>
    </section>
  );
}

function Labelled({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="labelled">
      <span className="label">{label}</span>
      <Code>{value}</Code>
    </div>
  );
}

/** A facet value on one line. Structured values fall back to compact JSON. */
function render(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null) return 'null';
  return JSON.stringify(value);
}

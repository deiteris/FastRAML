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
 *
 * **What stays in this file is what recurses through this file.** `ShapeView`,
 * `Body` and `Attribute` call each other in a cycle -- an object holds
 * attributes, an attribute holds a shape -- and splitting a cycle across
 * modules buys nothing but an import cycle. Everything that only *uses* a shape
 * renderer went to a module of its own: parameters, bodies, responses, security,
 * annotations, values.
 */

import { useState } from 'react';
import { Link } from 'react-router';
import {
  type Index,
  type Json,
  type Parameter,
  type PatternProperty,
  type Property,
  type Ref,
  type Shape,
  facetsOf,
  isRecursive,
  isRef,
  labelOf,
  spellingOf,
} from '../model';
import { Annotations } from './Annotations';
import { From } from './Borrowed';
import { Code, Labelled, facetValue, oneLine } from './json';
import { Prose, ProseInline } from './markdown';
import { Chip, type Tab, Tabs } from './ui';

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
   * The caller's own line already named the item type -- `tags string[]` -- so
   * the nested block would repeat it.
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
      <ProseInline className="reference-desc">{target?.description}</ProseInline>
      {expandable(target) && (
        <>
          <Expander open={open} onToggle={() => setOpen(!open)} />
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

/** The control that opens a reference in place. One shape, two call sites. */
function Expander({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="expander" aria-expanded={open} onClick={onToggle}>
      <span className="expander-sign">{open ? '−' : '+'}</span>
      {open ? 'Hide child attributes' : 'Show child attributes'}
    </button>
  );
}

/**
 * Whether an array's items are already said by its own type line.
 *
 * `tags string[]` needs no nested block: it was a label and a rule around one
 * word. An item with structure -- an inline object, a union, or a reference to
 * a type that has attributes -- gets one, because that block is the only place
 * its expander can live. Suppressing it for every `$ref` left `related Book[]`
 * and `priceHistory Prices` naming a type with no way to see inside it: the row
 * a reader wants is the item's, not the array's.
 *
 * A recursion marker gets a row too. It is a stop, so nothing expands, but the
 * row is where the marker says the structure repeats and where its `head`
 * links -- and `related: Book[]` inside `Book` is exactly that case, so
 * suppressing it left the one attribute that most needed saying so silent.
 */
function simpleItems(shape: Shape, index: Index): boolean {
  const items = shape.items;
  if (items === null || items === undefined) return true;
  if (isRecursive(items)) return false;
  if (isRef(items)) return !expandable(index.shape(items.$ref));
  return !items.properties && !items.any_of && !items.pattern_properties && !items.items;
}

/**
 * A type declared by JSON Schema, in the two forms it needs.
 *
 * Such a type "MUST NOT participate in type inheritance or specialization"
 * (spec, *Using XML and JSON Schemas*): the parser decodes no RAML facet from
 * it, so the shape itself carries no properties and a view that showed only
 * that reports a type made of nothing.
 *
 * **The projection is the default panel** -- the nearest RAML shape to the
 * schema (docs/10 § 6.3), where `$ref` has been resolved and `#/definitions/…`
 * is an ordinary nested type. The schema as written is the other panel,
 * because it is what the author will edit and the projection is a reading of
 * it. Neither is a substitute for the other, which is why both are here and
 * neither is buried.
 *
 * The `extends` line goes: it names the supertype the include produced, as
 * `json`, which is true and worth nothing.
 */
function JsonSchema({ shape, index }: { shape: Shape; index: Index }) {
  const panels: Tab[] = [];
  if (shape.projection) {
    panels.push({
      key: 'type',
      label: 'Type',
      body: <ShapeView shape={shape.projection} index={index} hideType />,
    });
  }
  if (shape.json_schema) {
    panels.push({ key: 'schema', label: 'JSON Schema', body: <Code>{shape.json_schema}</Code> });
  }
  return <Tabs label="declared by" items={panels} />;
}

const TYPE_JSON = 'json';

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
  // The `extends` line below carries it instead, where it is also a link.
  const typed = !hideType && !restates(shape, index);
  const headed = Boolean(typed || named);
  const attributes = properties.length > 0 || patterns.length > 0;
  const json = shape.type === TYPE_JSON;

  return (
    <div className="shape">
      {headed && (
        <div className="shape-head">
          {named && <span className="shape-display">{shape.display_name}</span>}
          {typed && <TypeChip shape={shape} index={index} borrowed={borrowed} />}
        </div>
      )}
      {/* Above the prose. What a type extends is the first thing about it,
          and below the description it arrived after everything that only makes
          sense once you know. */}
      {inherits.length > 0 && !json && (
        <div className="shape-line">
          <span className="label">extends</span>
          {inherits.map((parent, at) => (
            <RefLink key={at} parent={parent} index={index} />
          ))}
        </div>
      )}
      {!hideDescription && <Prose>{shape.description}</Prose>}

      {(facets.length > 0 || shape.enum) && (
        <div className="facets">
          {facets.map(([name, value]) => (
            <Chip key={name}>
              <span className="facet-name">{name}</span>
              <span className="facet-value">{facetValue(name, value)}</span>
            </Chip>
          ))}
          {shape.enum?.map((value, at) => (
            <Chip key={`enum-${at}`} tone="enum">
              {oneLine(value)}
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
              <span className="facet-value">{oneLine(value)}</span>
            </Chip>
          ))}
        </div>
      )}

      {shape.discriminator && <Discriminator shape={shape} index={index} />}

      <Annotations applied={shape.annotations} index={index} />

      {/* Above the attributes, not below. An example is the fastest way to
          understand a type, and last it read as belonging to whichever
          attribute happened to come final. */}
      {shape.default !== undefined && <Labelled label="default" value={shape.default} />}
      {shape.example !== undefined && <Labelled label="example" value={shape.example} />}
      <Examples examples={shape.examples} />

      {json && <JsonSchema shape={shape} index={index} />}

      {members.length > 0 && <Union members={members} index={index} />}

      {shape.items !== undefined && !hideItems && (
        <Group label="each item">
          <ShapeView shape={shape.items} index={index} />
        </Group>
      )}

      {/* Indented under the head where there is one, because they belong to the
          thing it names. `Shelf slot object` sat beside its own `position` and
          `book` rather than above them, with one rule around the pair saying
          only that both were inside the array. */}
      {attributes && (
        <div className={headed ? 'nested' : undefined}>
          <div className="attributes">
            {properties.map(([name, property]) => (
              <Attribute key={name} name={name} property={property} index={index} />
            ))}
            {patterns.map(([pattern, property]) => (
              <Attribute key={pattern} name={`/${property.pattern}/`} property={property} index={index} pattern />
            ))}
          </div>
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
export function Attribute({
  name,
  property,
  index,
  pattern,
  from,
}: {
  name: string;
  property: Property | PatternProperty | Parameter;
  index: Index;
  pattern?: boolean;
  /** Where this row came from, if it was not declared here. */
  from?: { label: string; title: string; secured?: boolean };
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
  const holds = target && target.type === 'array' ? spellingOf(target, index) : null;
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
          <span className="attr-type">{spellingOf(shape, index)}</span>
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
        {from && <From label={from.label} title={from.title} secured={from.secured} />}
      </div>
      {/* A description is not an attribute, so it does not live behind the
          control that expands them. Where the type is a reference the prose
          belongs to the target, and reading it used to require opening the
          attribute list first. The expanded body suppresses it, so it appears
          once either way. */}
      <ProseInline className="attr-desc">{described}</ProseInline>
      {expandable(target) && (
        <>
          <Expander open={open} onToggle={() => setOpen(!open)} />
          {open && (
            <div className="nested">
              <Body shape={target} index={index} hideType hideDescription />
            </div>
          )}
        </>
      )}
      {/* A reference to a named array -- `priceHistory: Prices` -- renders as a
          link and nothing else, so what it holds was reachable only through the
          array's own page. The item row belongs here, and the expander belongs
          on the item and not on the array. */}
      {target?.type === 'array' && !simpleItems(target, index) && (
        <Group label="each item">
          <ShapeView shape={target.items} index={index} />
        </Group>
      )}
      {inline && <Body shape={shape} index={index} hideType hideDescription hideItems={simpleItems(shape, index)} />}
    </div>
  );
}

/**
 * Whether the type chip would only say what the `extends` line says.
 *
 * `type: Entity` gives the expression `Entity` and one supertype named
 * `Entity`, so printing both puts the fact on the page twice -- once without
 * the link. `Money[]` over a supertype of `array` says something `extends` does
 * not and stays.
 */
export function restates(shape: Shape, index: Index): boolean {
  const inherits = shape.inherits ?? [];
  const only = inherits[0];
  return inherits.length === 1 && only !== undefined && spellingOf(shape, index) === labelOf(only, index);
}

/** The type name a reader recognises: the expression as written, else the kind. */
export function TypeChip({ shape, index, borrowed }: { shape: Shape; index: Index; borrowed?: boolean }) {
  const written = spellingOf(shape, index, borrowed);
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

/**
 * Named examples, as tabs.
 *
 * Stacked, each one is a code block the height of the value, so a type with
 * three of them pushed its own attributes off the screen -- and the reader
 * wanting one example was scrolling past two. One at a time, all names visible.
 *
 * A single example keeps its own labelled block: a tab strip with one tab is a
 * control that does nothing.
 */
function Examples({ examples }: { examples?: Record<string, Json> }) {
  const entries = Object.entries(examples ?? {});
  const [first] = entries;
  if (entries.length === 0) return null;
  if (entries.length === 1 && first) return <Labelled label={`example: ${first[0]}`} value={first[1]} />;
  return (
    <div className="labelled">
      <Tabs
        label="examples"
        items={entries.map(([name, value]) => ({ key: name, label: name, body: <Code>{value}</Code> }))}
      />
    </div>
  );
}

/**
 * The property that tells the subtypes of this type apart, and this type's
 * value for it.
 *
 * Two different statements, and only one belongs on each page. The type that
 * *declares* `discriminator` names the property and has no value of its own:
 * printing one there read `Publication by default`, which says nothing a reader
 * of the documentation wants and invites them to send it. A subtype has a
 * value, and that value is the only part they care about, so it is shown plain.
 *
 * `discriminatorValue` defaults to the subtype's own name (RAML 1.0 § 5.5). The
 * rule is applied in P10, where a language rule belongs, and is not written
 * onto the shape -- so the subtype case computes it rather than reading it.
 *
 * A subtype is told from the declaring type by looking up: after unwrap every
 * subtype carries an inherited `discriminator` and looks like a declaration
 * (the note in CLAUDE.md, and go-raml's `FIXME` for the same reason), so the
 * shape alone cannot say which it is.
 */
function Discriminator({ shape, index }: { shape: Shape; index: Index }) {
  const inherited = (shape.inherits ?? []).some((parent) => isRef(parent) && index.shape(parent.$ref)?.discriminator);
  const value = shape.discriminator_value ?? (inherited ? shape.name : null);
  return (
    <div className="shape-line">
      <span className="label">{inherited ? 'discriminated by' : 'discriminator'}</span>
      <code className="attr-name">{shape.discriminator}</code>
      {value !== null && (
        <>
          <span className="facet-name">=</span>
          <Chip tone="enum">{oneLine(value)}</Chip>
        </>
      )}
    </div>
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

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
 * **Three rules decide everything below.**
 *
 * *One name, one treatment.* Every place a type is named goes through
 * `TypeName`: the same expression, the same muted mono text, and a link
 * wherever the name identifies a declaration. A reader should not have to
 * learn that `Book[]` on one line and `Money` on the next are the same kind of
 * thing shown two ways, or that only one of them can be followed.
 *
 * The same holds for what a type *contains*. `Body` draws every kind's
 * structure -- the `each item` group, the `anyOf` selector, the attribute list
 * -- and an attribute row renders its inline type through `Body` rather than
 * arranging the parts itself. An array is one construct whether it is a
 * declaration, a response body or a property.
 *
 * *Declared here, shown here; named here, behind a control.* What a row
 * declares -- its facets, its enum, its own example, an inline object's
 * properties -- has nowhere else to be read, so it is open. What it names is
 * declared elsewhere and has a page, so it is one control away. That control is
 * `Expandable`, and it says what is behind it rather than assuming attributes.
 *
 * *What constrains the data and what extends the language are two bands.*
 * `maxLength 200` says what a payload may contain; `(deprecated): use PUT` and
 * `stewardedBy: catalogue-team` say nothing a request has to satisfy -- they are
 * there for some processor other than this one to act on. Both were drawn as
 * chips in the same row, so a reader scanning for what to send read three
 * constraints where there was one. Every extension point -- a `facets:`
 * declaration, a value supplied for one, an applied annotation -- goes below the
 * constraints and inside `Extra`, which is a region and not a row.
 *
 * **What stays in this file is what recurses through this file.** `ShapeView`,
 * `Body` and `Attribute` call each other in a cycle -- an object holds
 * attributes, an attribute holds a shape -- and splitting a cycle across
 * modules buys nothing but an import cycle. Everything that only *uses* a shape
 * renderer went to a module of its own: parameters, bodies, responses, security,
 * extension points, values.
 */

import { type ReactNode, useState } from 'react';
import { Link } from 'react-router';
import {
  type Example,
  type Index,
  type Parameter,
  type PatternProperty,
  type Property,
  type ObjectShape,
  type Ref,
  type Recursion,
  type Shape,
  contentOf,
  detailed,
  facetsOf,
  isRecursive,
  isRef,
  labelOf,
  leadsSomewhere,
  namedByItems,
  namedByMembers,
  spellingOf,
  MEMBERS_SPELLED,
} from '../model';
import { Extra } from './Extra';
import { From } from './Borrowed';
import { Code, Labelled, oneLine } from './json';
import { Prose, ProseInline } from './markdown';
import { Chip, Tabs } from './ui';

interface Props {
  shape: Shape | Ref | Recursion | null | undefined;
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
   * This shape's `type_expr` belongs to its container, so only its `type` is
   * its own. True of a union member and an inlined supertype -- see `spelling`.
   */
  borrowed?: boolean;
}

export function ShapeView({ shape, index, hideType, hideDescription, borrowed }: Props) {
  if (shape === null || shape === undefined) return <TypeName shape={shape} index={index} />;
  if (isRef(shape)) return <RefView node={shape} index={index} />;
  if (isRecursive(shape)) return <RecursionView node={shape} index={index} />;
  return <Body shape={shape} index={index} hideType={hideType} hideDescription={hideDescription} borrowed={borrowed} />;
}

/**
 * A link: a name that navigates, and a control that expands it in place.
 *
 * Both, because they answer different questions -- navigating loses your place
 * in a response body, expanding keeps it but buries the declaration's own page.
 * They are two visibly different controls for that reason: a link, and a
 * labelled button that says what it will open.
 *
 * Neither expands on render. That is the loop.
 */
function RefView({ node, index }: { node: Ref; index: Index }) {
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
      {detailed(target) && (
        <Expandable what={behind(target)}>
          <Body shape={target} index={index} hideType hideDescription />
        </Expandable>
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
function RecursionView({ node, index }: { node: Recursion; index: Index }) {
  return (
    <span className="reference">
      <TypeName shape={node} index={index} />
    </span>
  );
}

/**
 * One collapsed region and the control that opens it.
 *
 * On a row of its own, always: where a control appears must not depend on
 * whether someone wrote a description above it.
 *
 * `what` names what is inside rather than assuming it -- constraints, members,
 * item type, child attributes. "Show child attributes" over a `string` with a
 * `pattern` promises attributes that do not exist.
 */
function Expandable({ what, children }: { what: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="expand">
      <button type="button" className="expander" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="expander-sign">{open ? '−' : '+'}</span>
        {open ? `Hide ${what}` : `Show ${what}`}
      </button>
      {open && <div className="nested">{children}</div>}
    </div>
  );
}

/** What is behind a name, in the words of the kind it names. */
function behind(shape: Shape): string {
  const content = contentOf(shape);
  if (content.type === 'object' && (content.properties || content.pattern_properties)) return 'child attributes';
  if (content.type === 'union' && content.any_of) return 'members';
  if (content.type === 'array' && content.items !== undefined) return 'item type';
  return 'constraints';
}

/**
 * A type, named once and the same way everywhere.
 *
 * Every place a type is named goes through this: an attribute's head line, a
 * declaration's heading, a listing's row, an array's items. One treatment, so
 * the same type reads the same wherever it appears.
 *
 * **A name that identifies a declaration is a link.** `related Book[]` and
 * `price Money` both name a type with a page; whether the author happened to
 * write the reference as a bare name or inside an array expression is not a
 * reason for one of them to be a dead end.
 *
 * `suffix` is carried down rather than appended by the caller, so the `[]` of
 * an array lands on the item's *name* and before any marker that follows it --
 * `Book[]` with the recursion chip after the whole expression, not `Book` chip
 * `[]`.
 */
export function TypeName({
  shape,
  index,
  borrowed,
  suffix = '',
}: {
  shape: Shape | Ref | Recursion | null | undefined;
  index: Index;
  borrowed?: boolean;
  suffix?: string;
}) {
  if (shape === null || shape === undefined) return <span className="attr-type">any{suffix}</span>;
  if (isRef(shape)) {
    const entry = index.get(shape.$ref);
    const target = index.shape(shape.$ref);
    if (!entry) {
      return (
        <Chip tone="warn" title={shape.$ref}>
          unresolved
        </Chip>
      );
    }
    return (
      <>
        <Link to={entry.href} className="typelink">
          {entry.name}
          {suffix}
        </Link>
        {/* What a named array holds, on the line that names it. `priceHistory
            Prices` was a link to a type whose whole content is its item type,
            so the one fact the row was missing sat one click away. */}
        {target?.type === 'array' && <TypeName shape={target.items} index={index} suffix="[]" />}
      </>
    );
  }
  if (isRecursive(shape)) {
    const entry = index.get(shape.head.$ref);
    return (
      <>
        {entry ? (
          <Link to={entry.href} className="typelink">
            {entry.name}
            {suffix}
          </Link>
        ) : (
          <span className="attr-type">
            {shape.name ?? index.label(shape.head.$ref)}
            {suffix}
          </span>
        )}
        <Chip tone="recursive" title="the structure repeats from here; it is not expanded">
          recursive
        </Chip>
      </>
    );
  }
  // A name written as an expression is still a name. `type: Entity` gives a
  // shape that is not a `$ref`, so the listing printed `Entity` as grey text
  // beside `Money[]` as a link -- two names of declarations, one reachable.
  const only = (shape.inherits ?? [])[0];
  if (!borrowed && only !== undefined && isRef(only) && restates(shape, index)) {
    return <TypeName shape={only} index={index} suffix={suffix} />;
  }
  // An array defers to its items wherever its own expression says no more than
  // they do, so `Review[]` is the link `Review` and not four grey characters.
  //
  // `borrowed`, because the items of an array *written as an expression* carry
  // the whole expression: `sources?: string[]` gives items whose `type_expr` is
  // `string[]`, not `string`. Without it the suffix was appended to a spelling
  // that already had one and the row read `string[][]`. It is the same trap
  // `labelOf` names for a union member, which passes `true` for this reason;
  // only the array half was missing it. An array written `type: array` was
  // unaffected, its items carrying their own `string`, which is why every
  // sample array read correctly until one was written as an expression.
  if (shape.type === 'array' && namedByItems(shape, borrowed ?? false)) {
    return <TypeName shape={shape.items} index={index} suffix={`[]${suffix}`} borrowed />;
  }
  if (shape.type === 'union' && shape.any_of && shape.any_of.length > 0 && namedByMembers(shape, borrowed ?? false)) {
    return <UnionName members={shape.any_of} index={index} suffix={suffix} />;
  }
  return (
    <span className="attr-type" title={spellingOf(shape, index, borrowed) === shape.type ? undefined : `a ${shape.type}`}>
      {spellingOf(shape, index, borrowed)}
      {suffix}
    </span>
  );
}

/**
 * A union's name: its members, each reachable, and a count for the rest.
 *
 * Spelled from `any_of` and not from `type_expr`, for the reason `spellingOf`
 * gives: the author's text is one unbreakable run whose length is theirs to
 * choose, and it names nothing a reader can follow. The `anyOf` selector below
 * carries the whole list; this is the name.
 */
function UnionName({ members, index, suffix }: { members: (Shape | Ref | Recursion)[]; index: Index; suffix: string }) {
  const shown = members.slice(0, MEMBERS_SPELLED);
  const rest = members.length - shown.length;
  return (
    <span className="union-name">
      {shown.map((member, at) => (
        <span key={at}>
          {at > 0 && <span className="union-bar">|</span>}
          <TypeName shape={member} index={index} borrowed />
        </span>
      ))}
      {rest > 0 && (
        <span className="attr-type" title={members.map((member) => labelOf(member, index)).join(' | ')}>
          <span className="union-bar">|</span>+{rest} more
        </span>
      )}
      {suffix && <span className="attr-type">{suffix}</span>}
    </span>
  );
}

function Body({
  shape,
  index,
  hideType,
  hideDescription,
  hideInherits,
  borrowed,
}: {
  shape: Shape;
  index: Index;
  hideType?: boolean;
  hideDescription?: boolean;
  /** The caller's own line is a link to the one supertype -- see `Attribute`. */
  hideInherits?: boolean;
  borrowed?: boolean;
}) {
  // Identity and prose come from the declaration; structure and constraint come
  // from its content. The two are the same shape for sixteen of the seventeen
  // kinds. For a JSON-schema type they are the declaration and its projection:
  // the spec forbids a RAML facet beside a schema, so the declaration carries
  // the name, the description and the example, and everything else it says is
  // in the projection (docs/10 § 6.3). Reading structure through one accessor
  // is what keeps that a fact about `contentOf` rather than a branch in every
  // block below.
  const content = contentOf(shape);
  const facets = facetsOf(content);
  const inherits = shape.inherits ?? [];
  const properties = Object.entries(content.type === 'object' ? (content.properties ?? {}) : {});
  const patterns = Object.entries(content.type === 'object' ? (content.pattern_properties ?? {}) : {});
  const declared = Object.entries(shape.declared_facets ?? {});
  const members = content.type === 'union' ? (content.any_of ?? []) : [];
  // `hideType` means the container names this shape, so its `displayName`
  // belongs up there too. Rendered here it was a bare word between the
  // description and the facets, with nothing saying what it was.
  const named = !hideType && shape.display_name && shape.display_name !== shape.name;
  // The `extends` line below carries it instead, where it is also a link.
  const typed = !hideType && !restates(shape, index);
  const headed = Boolean(typed || named);
  const attributes = properties.length > 0 || patterns.length > 0;
  const schema = shape.type === 'json' ? shape.json_schema : undefined;
  const attributeList = (
    <div className="attributes">
      {properties.map(([name, property]) => (
        <Attribute key={name} name={name} property={property} index={index} />
      ))}
      {patterns.map(([pattern, property]) => (
        <Attribute key={pattern} name={`/${property.pattern}/`} property={property} index={index} pattern />
      ))}
    </div>
  );

  return (
    <div className="shape">
      {headed && (
        <div className="shape-head">
          {named && <span className="shape-display">{shape.display_name}</span>}
          {typed && <TypeName shape={shape} index={index} borrowed={borrowed} />}
        </div>
      )}
      {/* Above the prose. What a type extends is the first thing about it,
          and below the description it arrived after everything that only makes
          sense once you know.

          A schema type has none to show. `!include schema.json` produces an
          anonymous supertype that *is* the schema, so it carries the same
          projection this shape does and renders the whole type a second
          time. */}
      {inherits.length > 0 && !hideInherits && content === shape && (
        <div className="shape-line">
          <span className="label">extends</span>
          {inherits.map((parent, at) => (
            <RefLink key={at} parent={parent} index={index} />
          ))}
        </div>
      )}
      {!hideDescription && <Prose>{shape.description}</Prose>}
      {/* A schema describes itself, and the RAML declaring it describes why
          it is here. Both are authored text and neither is the other. */}
      {content !== shape && <Prose>{content.description}</Prose>}

      {facets.length > 0 && (
        <div className="facets">
          {facets.map(([name, value]) => (
            <Chip key={name}>
              <span className="facet-name">{name}</span>
              <span className="facet-value">{oneLine(value)}</span>
            </Chip>
          ))}
        </div>
      )}

      {/* Labelled, and on a line of its own. Among the facet chips these were a
          row of bare words -- `pending sent delivered` under a string, with
          nothing saying whether they were a list of values, of names, or of
          anything else. Every other facet carries its own name; an enum's
          values are the one thing that arrived without one. */}
      {content.enum && content.enum.length > 0 && (
        <div className="shape-line">
          <span className="label">allowed values</span>
          {content.enum.map((value, at) => (
            <Chip key={at} tone="enum">
              {oneLine(value)}
            </Chip>
          ))}
        </div>
      )}

      {shape.allowed_targets && <Tagged label="allowedTargets" values={shape.allowed_targets} />}

      {content.type === 'object' && content.discriminator && <Discriminator shape={content} index={index} />}

      {/* Everything the author added, in one region and outside the facet band
          above: the annotations, the values supplied for a custom facet, and
          the `facets:` block that declares one. A supplied facet rendered as a
          chip beside `maxLength 200` claimed to constrain a payload, and an
          annotation beside it claimed the same; neither does. A `facets:` block
          drawn as its own attribute list made the third version of the same
          mistake -- beside the properties, in the properties' own treatment, it
          read as more of them. */}
      <Extra
        applied={shape.annotations}
        facets={content.custom_facets}
        // Only when there is one: a React element is truthy even where its
        // component returns null, so passing it unconditionally would open an
        // empty region on every type that declares no facet.
        declares={declared.length > 0 ? <Declares facets={declared} index={index} /> : undefined}
        owner={shape}
        index={index}
      />

      {/* Above the attributes, not below. An example is the fastest way to
          understand a type, and last it read as belonging to whichever
          attribute happened to come final. */}
      {shape.default !== undefined && <Labelled label="default" value={shape.default} />}
      {shape.example && <OneExample label="example" example={shape.example} />}
      <Examples examples={shape.examples} />

      {/* The schema as written, behind the same control everything else is
          behind. The projection above is a reading of it; this is the text an
          author edits, and `$ref` is unresolved in it. */}
      {schema !== undefined && schema !== null && (
        <Expandable what="JSON Schema">
          <Code language="json">{schema}</Code>
        </Expandable>
      )}

      {members.length > 0 && <Union members={members} index={index} />}

      {/* What an array holds, wherever an array appears -- a declaration page, a
          response body, an attribute row. One construct, because an array is
          one thing and a reader should not have to learn that `Book[]` in a
          body and `items Anything[]` in an attribute list disclose their item
          type two different ways.

          Only where the items say something the head line did not. `string[]`
          named its item on the head and then drew a label and a rule around
          the word `string`. */}
      {content.type === 'array' && leadsSomewhere(content.items, index) && (
        <Group label="each item">
          {/* The array's head already names its items (`object[]`, `Book[]`).
              Repeating that name here added a line and another indentation
              level before any item constraint or property appeared. */}
          <ShapeView shape={content.items} index={index} hideType />
        </Group>
      )}

      {/* Indented under the head where there is one, because they belong to the
          thing it names. With no head, keep the list directly under `.shape`:
          the one-rule-per-level CSS relies on that adjacency to avoid drawing
          another border inside the one the owning attribute already draws. */}
      {attributes && (headed ? <div className="nested">{attributeList}</div> : attributeList)}

      {content.xml !== undefined && <Labelled label="xml" value={content.xml} />}
    </div>
  );
}

/**
 * One named thing -- a property, a pattern property, a parameter, a header.
 *
 * Name, type and whether it is required on one line; prose under it; anything
 * nested under that again. Everything a reader scans for sits in one column,
 * which is what a table put in three.
 *
 * **Where a thing was declared decides whether it is open.** What this row
 * declares -- its facets, its enum, its own example, an inline object's
 * properties -- is written here and is shown here; a reader has no other place
 * to find it. What it *names* is declared elsewhere, has a page of its own, and
 * sits behind one control, so a list of attributes stays a list of attributes
 * rather than an unrolled copy of every type it mentions.
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
  const required = 'required' in property ? property.required : false;
  const shape = property.type;
  const ref = shape !== null && shape !== undefined && isRef(shape) ? shape : null;
  const target = ref ? index.shape(ref.$ref) : undefined;
  const inline = shape !== null && shape !== undefined && !isRef(shape) && !isRecursive(shape) ? shape : null;
  const described = inline ? inline.description : target?.description;

  return (
    <div className="attr">
      {/* Name, type, flag -- in that order, always. Letting a reference render
          itself here put its expander between the type and `Required`, so the
          one word a reader scans for moved depending on whether the type
          happened to be a link. */}
      <div className="attr-head">
        <code className="attr-name">{name}</code>
        {inline?.display_name && inline.display_name !== inline.name && (
          <span className="attr-display">{inline.display_name}</span>
        )}
        <TypeName shape={shape} index={index} />
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
      {/* `hideInherits` where the head line is already a link to the one
          supertype: a query parameter typed `Search` read `Search` above
          `EXTENDS Search`, the same word twice with nothing between them. */}
      {/* Everything this row declares, including what an inline array holds:
          `Body` draws the `each item` group, so the treatment is the one a
          declaration page and a response body already use. */}
      {inline && (
        <Body shape={inline} index={index} hideType hideDescription hideInherits={restates(inline, index)} />
      )}
      {/* The one control. A named type's whole body is behind it -- which for
          an array is its own facets and then its items, so `priceHistory
          Prices` opens once onto everything `Prices` is. */}
      {detailed(target) && (
        <Expandable what={behind(target)}>
          <Body shape={target} index={index} hideType hideDescription />
        </Expandable>
      )}
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
  // A schema type shows no supertype, so there is nothing for its name to
  // restate: `Invoice` reads `object`, from the projection, and not `json`.
  if (contentOf(shape) !== shape) return false;
  const inherits = shape.inherits ?? [];
  const only = inherits[0];
  return inherits.length === 1 && only !== undefined && spellingOf(shape, index) === labelOf(only, index);
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
function Union({ members, index }: { members: (Shape | Ref | Recursion)[]; index: Index }) {
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
 * - anything that says no more than its name -- see `detailed`.
 *
 * The link still matters in both: the target has a page, with its own prose and
 * examples and the list of what else points at it.
 *
 * An anonymous shape is not a link and not a declaration -- the `integer` in
 * `type: integer | number` after P9 distributes a facet exists nowhere else --
 * so it is rendered where it sits.
 */
function RefLink({ parent, index }: { parent: Shape | Ref | Recursion; index: Index }) {
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
function Examples({ examples }: { examples?: Record<string, Example> }) {
  const entries = Object.entries(examples ?? {});
  const [first] = entries;
  if (entries.length === 0) return null;
  if (entries.length === 1 && first) return <OneExample label={`example: ${first[0]}`} example={first[1]} />;
  return (
    <div className="labelled">
      <Tabs
        label="examples"
        items={entries.map(([name, example]) => ({
          // The author's own name for it where there is one. `displayName` is
          // written to be read, and the key beside it is often `ex1`.
          key: name,
          label: example.display_name ?? name,
          body: <ExampleBody example={example} />,
        }))}
      />
    </div>
  );
}

/**
 * One example: its value, and what the author wrote beside it.
 *
 * `strict: false` is the reason such an example is in the document at all -- it
 * marks one that deliberately does not validate -- so it is a flag on the
 * example and not a footnote. The prose is the author's own description of what
 * the value shows.
 */
function OneExample({ label, example }: { label: string; example: Example }) {
  return (
    <div className="labelled">
      <div className="shape-line">
        <span className="label">{label}</span>
        {example.display_name && <span className="attr-display">{example.display_name}</span>}
        {example.strict === false && <Chip tone="warn">not validated</Chip>}
      </div>
      <ProseInline className="attr-desc">{example.description}</ProseInline>
      <Code>{example.value}</Code>
    </div>
  );
}

/** The body of one tab: the same content, without the label its tab supplies. */
function ExampleBody({ example }: { example: Example }) {
  return (
    <>
      {example.strict === false && (
        <div className="shape-line">
          <Chip tone="warn">not validated</Chip>
        </div>
      )}
      <ProseInline className="attr-desc">{example.description}</ProseInline>
      <Code>{example.value}</Code>
    </>
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
 * (the note in CLAUDE.md, and go-raml carries a `FIXME` for the same reason), so the
 * shape alone cannot say which it is.
 */
function Discriminator({ shape, index }: { shape: ObjectShape; index: Index }) {
  const inherited = (shape.inherits ?? []).some((parent) => {
    const target = isRef(parent) ? index.shape(parent.$ref) : undefined;
    return target?.type === 'object' && Boolean(target.discriminator);
  });
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

/**
 * A `facets:` block: what a *subtype* of this type must supply (docs/10 § 4).
 *
 * Rows and not chips, because a `facets:` entry is a property in every respect
 * -- it has a type, it is required or it is not, and it may be an object with
 * properties of its own -- and the names alone said none of that.
 *
 * It renders inside `Extra` rather than beside the attribute list, where the
 * same rows in the same treatment read as more properties. **The distinction is
 * the one that catches people**: nothing here constrains a payload, and this
 * type neither has to satisfy its own declaration nor may supply a value for it.
 * The sentence under the label says so, because a reader looking at `Entity` has
 * no other way to learn it -- the values live on `Book`.
 */
function Declares({ facets, index }: { facets: [string, Property][]; index: Index }) {
  return (
    <div className="declares">
      <span className="label">user-defined facets</span>
      <div className="attributes">
        {facets.map(([name, facet]) => (
          <Attribute key={name} name={name} property={facet} index={index} />
        ))}
      </div>
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

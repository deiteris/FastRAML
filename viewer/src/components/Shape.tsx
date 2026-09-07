/**
 * The type renderer -- the one component the rest of the app is arranged around.
 *
 * It implements the traversal law directly (docs/16 § 11.7): descend
 * containment, follow a link only when asked, stop at a recursion marker. There
 * is no ancestor set and no depth budget anywhere below, because the emitter
 * guarantees a cycle is always *marked* -- an unmarked one would hang this, and
 * that is the property `tests/unit/test_consumer_traversal.py` pins.
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
  spelling,
} from '../model';
import { Chip, Code, Disclosure, Prose } from './ui';

interface Props {
  shape: Shape | Ref | null | undefined;
  index: Index;
  /**
   * What the surrounding context already displays, so this does not repeat it.
   *
   * Two independent flags rather than one, because the two callers that need
   * them need different halves: a property table has a Description column and
   * shows the type in its own cell, while a declaration page puts the type in
   * the heading and wants the prose. One flag for both printed every header's
   * description twice; no flag at all printed `string | number` under a
   * heading that had just said it.
   */
  hideType?: boolean;
  hideDescription?: boolean;
  /**
   * The enclosing shape's `type_expr`. A nested shape carrying the same one did
   * not declare it, so its own `type` is what to show -- see `spelling`.
   */
  inherited?: string;
}

export function ShapeView({ shape, index, hideType, hideDescription, inherited }: Props) {
  if (shape === null || shape === undefined) return <Chip tone="type">any</Chip>;
  if (isRef(shape)) return <RefView node={shape} index={index} />;
  if (isRecursive(shape)) return <RecursionView node={shape} index={index} />;
  return (
    <Body
      shape={shape}
      index={index}
      hideType={hideType}
      hideDescription={hideDescription}
      inherited={inherited}
    />
  );
}

/** What to call one member of a union, in a tab or a list. */
function labelOf(member: Shape | Ref, index: Index, inherited?: string): string {
  if (isRef(member)) return index.label(member.$ref);
  if (isRecursive(member)) return member.name ?? 'recursive';
  return spelling(member, inherited);
}

/**
 * A link: a name that navigates, and a disclosure that expands it in place.
 *
 * Both, because they answer different questions. Navigating loses your place in
 * a response body; expanding keeps it but buries the declaration's own page.
 * Neither expands on render -- that is the loop.
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
  const label = (
    <Link to={entry.href} className="typelink">
      {entry.name}
    </Link>
  );
  if (!target) return label;
  return (
    <Disclosure summary={label}>
      <Body shape={target} index={index} />
    </Disclosure>
  );
}

/**
 * The marker that says the structure repeats.
 *
 * Rendered as a stop, never as an expandable link: `head` points at something
 * the walk is already inside, so expanding it here is the loop by another name.
 */
function RecursionView({ node, index }: { node: Shape & { head: Ref }; index: Index }) {
  const entry = index.get(node.head.$ref);
  return (
    <span className="recursion">
      <Chip tone="recursive" title="the structure repeats from here">
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

function Body({
  shape,
  index,
  hideType,
  hideDescription,
  inherited,
}: {
  shape: Shape;
  index: Index;
  hideType?: boolean;
  hideDescription?: boolean;
  inherited?: string;
}) {
  //: What every shape below this one inherits, if it declares no expression of
  //: its own. Read once here rather than at each descent.
  const own = typeof shape.type_expr === 'string' ? shape.type_expr : undefined;
  const facets = facetsOf(shape);
  const inherits = shape.inherits ?? [];
  const properties = Object.entries(shape.properties ?? {});
  const patterns = Object.entries(shape.pattern_properties ?? {});
  const members = shape.any_of ?? [];

  return (
    <div className="shape">
      <div className="shape-head">
        {shape.display_name && shape.display_name !== shape.name && (
          <span className="shape-display">{shape.display_name}</span>
        )}
        {!hideType && <TypeChip shape={shape} inherited={inherited} />}
      </div>
      {!hideDescription && <Prose>{shape.description}</Prose>}

      {inherits.length > 0 && (
        <div className="shape-line">
          <span className="label">extends</span>
          {inherits.map((parent, at) => (
            <span key={at} className="inherit">
              <ShapeView shape={parent} index={index} inherited={own} />
            </span>
          ))}
        </div>
      )}

      {facets.length > 0 && (
        <div className="facets">
          {facets.map(([name, value]) => (
            <Chip key={name} tone="plain">
              <span className="facet-name">{name}</span>
              <span className="facet-value">{render(value)}</span>
            </Chip>
          ))}
        </div>
      )}

      {shape.enum && (
        <div className="shape-line">
          <span className="label">enum</span>
          {shape.enum.map((value, at) => (
            <Chip key={at}>{render(value)}</Chip>
          ))}
        </div>
      )}

      {shape.allowed_targets && (
        <div className="shape-line">
          <span className="label">allowedTargets</span>
          {shape.allowed_targets.map((target) => (
            <Chip key={target}>{target}</Chip>
          ))}
        </div>
      )}

      {shape.declares_facets && (
        <div className="shape-line">
          <span className="label">declares facets</span>
          {shape.declares_facets.map((name) => (
            <Chip key={name}>{name}</Chip>
          ))}
        </div>
      )}

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

      {members.length > 0 && <Union members={members} index={index} inherited={own} />}

      {shape.items !== undefined && (
        <div className="members">
          <span className="label">items</span>
          <div className="member">
            <ShapeView shape={shape.items} index={index} inherited={own} />
          </div>
        </div>
      )}

      {(properties.length > 0 || patterns.length > 0) && (
        <table className="properties">
          <thead>
            <tr>
              <th>Property</th>
              <th>Type</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {properties.map(([name, property]) => (
              <PropertyRow key={name} name={name} property={property} index={index} inherited={own} />
            ))}
            {patterns.map(([pattern, property]) => (
              <PropertyRow
                key={pattern}
                name={`/${property.pattern}/`}
                property={property}
                index={index}
                inherited={own}
                pattern
              />
            ))}
          </tbody>
        </table>
      )}

      {shape.default !== undefined && <Labelled label="default" value={shape.default} />}
      {shape.example !== undefined && <Labelled label="example" value={shape.example} />}
      {shape.examples &&
        Object.entries(shape.examples).map(([name, value]) => (
          <Labelled key={name} label={`example: ${name}`} value={value} />
        ))}
      {shape.xml !== undefined && <Labelled label="xml" value={shape.xml} />}
    </div>
  );
}

function PropertyRow({
  name,
  property,
  index,
  pattern,
  inherited,
}: {
  name: string;
  property: Property | PatternProperty;
  index: Index;
  pattern?: boolean;
  inherited?: string;
}) {
  const required = 'required' in property ? property.required : false;
  const type = property.type;
  const description = type && !isRef(type) ? type.description : undefined;
  return (
    <tr>
      <td className="property-name">
        <code>{name}</code>
        {pattern ? (
          <Chip tone="optional">pattern</Chip>
        ) : (
          <Chip tone={required ? 'required' : 'optional'}>{required ? 'required' : 'optional'}</Chip>
        )}
      </td>
      <td className="property-type">
        <ShapeView shape={type} index={index} hideDescription inherited={inherited} />
      </td>
      <td className="property-description">{description}</td>
    </tr>
  );
}

/** The type name a reader recognises: the expression as written, else the kind. */
export function TypeChip({ shape, inherited }: { shape: Shape; inherited?: string }) {
  const written = spelling(shape, inherited);
  return (
    <Chip tone="type" title={written === shape.type ? undefined : `a ${shape.type}`}>
      {written}
    </Chip>
  );
}

/**
 * A union, as a selector over its members.
 *
 * **`anyOf`, not "one of".** The model's field is `any_of`, the JSON says
 * `any_of`, and this view uses the model's own vocabulary throughout (docs/16
 * § 11.9). They also do not mean the same thing: a value satisfying more than
 * one member is still valid, which "one of" denies.
 *
 * A selector rather than a stack because a union member is a whole type. Two
 * object members rendered one after another produce two property tables with
 * nothing between them saying where the first ended. One at a time, with the
 * alternatives always visible, is what makes a union readable at all.
 */
function Union({ members, index, inherited }: { members: (Shape | Ref)[]; index: Index; inherited?: string }) {
  const [chosen, setChosen] = useState(0);
  const at = Math.min(chosen, members.length - 1);
  return (
    <div className="union">
      <div className="union-tabs">
        <span className="label">anyOf</span>
        {members.map((member, position) => (
          <button
            key={position}
            type="button"
            className={`union-tab ${position === at ? 'is-chosen' : ''}`}
            onClick={() => setChosen(position)}
          >
            {labelOf(member, index, inherited)}
          </button>
        ))}
      </div>
      <div className="union-member">
        <ShapeView shape={members[at]} index={index} hideType inherited={inherited} />
      </div>
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
            {one.value !== null && one.value !== undefined && (
              <span className="facet-value">{render(one.value)}</span>
            )}
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
    <div className="parameters">
      <h4>{title}</h4>
      <table className="properties">
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, parameter]) => {
            const type = parameter.type;
            return (
              <tr key={name}>
                <td className="property-name">
                  <code>{name}</code>
                  <Chip tone={parameter.required ? 'required' : 'optional'}>
                    {parameter.required ? 'required' : 'optional'}
                  </Chip>
                </td>
                <td className="property-type">
                  <ShapeView shape={type} index={index} hideDescription />
                </td>
                <td className="property-description">{type && !isRef(type) ? type.description : undefined}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
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

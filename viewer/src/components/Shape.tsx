/**
 * The type renderer -- the one component the rest of the app is arranged around.
 *
 * It implements the traversal law directly (docs/16 § 11.7): descend
 * containment, follow a link only when asked, stop at a recursion marker. There
 * is no ancestor set and no depth budget anywhere below, because the emitter
 * guarantees a cycle is always *marked* -- an unmarked one would hang this, and
 * that is the property `tests/unit/test_consumer_traversal.py` pins.
 */

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
  /** Suppress the heading when the surrounding row already carries the name. */
  bare?: boolean;
  /**
   * Suppress the description too, for a table that has a column for it.
   * Separate from `bare`: a request body is `bare` and wants its prose, so one
   * flag for both printed every header's description twice.
   */
  quiet?: boolean;
}

export function ShapeView({ shape, index, bare, quiet }: Props) {
  if (shape === null || shape === undefined) return <Chip tone="type">any</Chip>;
  if (isRef(shape)) return <RefView node={shape} index={index} />;
  if (isRecursive(shape)) return <RecursionView node={shape} index={index} />;
  return <Body shape={shape} index={index} bare={bare} quiet={quiet} />;
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
      <Body shape={target} index={index} bare />
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

function Body({ shape, index, bare, quiet }: { shape: Shape; index: Index; bare?: boolean; quiet?: boolean }) {
  const facets = facetsOf(shape);
  const inherits = shape.inherits ?? [];
  const properties = Object.entries(shape.properties ?? {});
  const patterns = Object.entries(shape.pattern_properties ?? {});
  const members = shape.any_of ?? [];

  return (
    <div className="shape">
      {!bare && (
        <div className="shape-head">
          {shape.display_name && shape.display_name !== shape.name && (
            <span className="shape-display">{shape.display_name}</span>
          )}
          <TypeChip shape={shape} />
        </div>
      )}
      {bare && <TypeChip shape={shape} />}
      {!quiet && <Prose>{shape.description}</Prose>}

      {inherits.length > 0 && (
        <div className="shape-line">
          <span className="label">extends</span>
          {inherits.map((parent, at) => (
            <span key={at} className="inherit">
              <ShapeView shape={parent} index={index} bare />
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

      {members.length > 0 && (
        <div className="members">
          <span className="label">one of</span>
          {members.map((member, at) => (
            <div key={at} className="member">
              <ShapeView shape={member} index={index} bare />
            </div>
          ))}
        </div>
      )}

      {shape.items !== undefined && (
        <div className="members">
          <span className="label">items</span>
          <div className="member">
            <ShapeView shape={shape.items} index={index} bare />
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
              <PropertyRow key={name} name={name} property={property} index={index} />
            ))}
            {patterns.map(([pattern, property]) => (
              <PropertyRow key={pattern} name={`/${property.pattern}/`} property={property} index={index} pattern />
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
}: {
  name: string;
  property: Property | PatternProperty;
  index: Index;
  pattern?: boolean;
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
        <ShapeView shape={type} index={index} bare quiet />
      </td>
      <td className="property-description">{description}</td>
    </tr>
  );
}

/** The type name a reader recognises: the expression as written, else the kind. */
export function TypeChip({ shape }: { shape: Shape }) {
  const written = spelling(shape);
  return (
    <Chip tone="type" title={written === shape.type ? undefined : `a ${shape.type}`}>
      {written}
    </Chip>
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
                  <ShapeView shape={type} index={index} bare quiet />
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

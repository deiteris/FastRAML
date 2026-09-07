/**
 * `securedBy`, in the two forms a page needs.
 *
 * It is a **disjunction** -- a caller satisfies any one entry, not all of them
 * -- so an operation gets a selector and not a list. `is_null` is
 * `securedBy: [null]`, how an author says a resource may also be called
 * unauthenticated (docs/09 A3); it binds to a real definition of type `null`,
 * so a view keeping only names would render it as a scheme called "null".
 */

import { Link } from 'react-router';
import type { Index, SecuredBy, SecurityScheme } from '../model';
import { Prose } from './markdown';
import { Chip, Lock } from './ui';

/**
 * The selector, above the parameters it changes.
 *
 * Choosing changes the page. A scheme is not only a gate: its `describedBy`
 * declares headers, query parameters and responses the operation *gains* when
 * secured that way, and those are merged into the operation's own tables below,
 * each marked with the scheme it came from. Shown in a section of their own
 * they pushed the operation's own parameters below the fold and made the reader
 * assemble the request from two places.
 */
export function SecurityChoice({
  schemes,
  declared,
  chosen,
  onChoose,
  index,
}: {
  schemes: SecuredBy[];
  declared?: SecurityScheme;
  chosen: number;
  onChoose: (at: number) => void;
  index: Index;
}) {
  if (schemes.length === 0) return null;
  const scheme = schemes[chosen];
  const entry = index.get(scheme?.declaration);
  return (
    <div className="security-choice">
      {/* The choice on its own row, the consequences under it. Trailing the
          type, a link and the scopes onto the same line wrapped badly the
          moment there were three schemes or more than two scopes, which is the
          ordinary case rather than the exceptional one. */}
      <div className="security-pick">
        <Lock open={schemes.some((one) => one.is_null)} />
        <span className="label">secured by</span>
        {schemes.map((one, at) => (
          <button
            key={at}
            type="button"
            className={`scheme ${at === chosen ? 'is-chosen' : ''}`}
            onClick={() => onChoose(at)}
          >
            {one.is_null ? 'None' : one.name}
          </button>
        ))}
      </div>

      <div className="security-detail">
        {scheme?.is_null ? (
          <p className="prose">
            This operation may be called unauthenticated. Everything below is what it requires without a token.
          </p>
        ) : (
          <>
            <div className="shape-line">
              {declared && <Chip tone="type">{declared.type}</Chip>}
              {entry && (
                <Link to={entry.href} className="typelink">
                  {entry.name}
                </Link>
              )}
            </div>
            <Prose>{declared?.description}</Prose>
            {/* `null` is "not narrowed" and `[]` is "narrowed to nothing"; the
                two are different and the emitter keeps them apart. */}
            {scheme && scheme.scopes !== null && (
              <div className="shape-line">
                <span className="label">scopes</span>
                {scheme.scopes.length === 0 ? (
                  <Chip tone="warn">narrowed to none</Chip>
                ) : (
                  scheme.scopes.map((scope) => <Chip key={scope}>{scope}</Chip>)
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** The one-line form, for a resource -- which has no request to change. */
export function SecuredByList({ schemes, index }: { schemes?: SecuredBy[]; index: Index }) {
  if (!schemes || schemes.length === 0) return null;
  return (
    <div className="shape-line">
      <Lock open={schemes.some((scheme) => scheme.is_null)} title="secured" />
      <span className="label">secured by</span>
      {schemes.map((scheme, at) => (
        <span key={at} className="secured">
          {at > 0 && <span className="or">or</span>}
          {scheme.is_null ? (
            <Chip tone="optional">unauthenticated</Chip>
          ) : (
            <SchemeName scheme={scheme} index={index} />
          )}
        </span>
      ))}
    </div>
  );
}

function SchemeName({ scheme, index }: { scheme: SecuredBy; index: Index }) {
  const entry = index.get(scheme.declaration);
  if (!entry) return <Chip tone={scheme.bound ? 'plain' : 'warn'}>{scheme.name}</Chip>;
  return (
    <Link to={entry.href} className="typelink">
      {entry.name}
    </Link>
  );
}

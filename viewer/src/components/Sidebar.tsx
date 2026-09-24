/**
 * The navigation: endpoints by path, then the declarations.
 *
 * Endpoints nest, because a path does. `document.endpoints` is flat and keyed
 * by the full path -- that is what the model holds after P6 propagated URI
 * parameters down -- so the nesting is rebuilt from the keys by `pathTree`.
 */

import { useMemo, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router';
import { type Document, type Index, type PathNode, declarations, methodsOf, pathTree } from '../model';
import { Chevron, ThemeToggle, Verb } from './ui';

export function Sidebar({
  document,
  index,
}: {
  document: Document;
  index: Index;
}) {
  const [filter, setFilter] = useState('');
  const roots = useMemo(() => pathTree(document.endpoints), [document]);
  const matches = (text: string) => !filter || text.toLowerCase().includes(filter.toLowerCase());

  const docs = (document.entry_point?.documentation ?? [])
    .map((item, at) => ({ item, at }))
    .filter(({ item }) => matches(item.title));
  const types = declarations(document.types).filter(({ name }) => matches(name));
  const annotationTypes = declarations(document.annotation_types).filter(({ name }) => matches(name));
  const schemes = declarations(document.security_schemes).filter(({ name }) => matches(name));

  return (
    <nav className="sidebar" id="sidebar">
      <Link to="/" className="brand">
        {document.entry_point?.title ?? 'API reference'}
      </Link>
      <input
        className="filter"
        type="search"
        placeholder="Filter…"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />

      {/* A destination among the others, not only the thing the title happens
          to link to. What the API is -- its base URI, its media types, its
          default security, its counts -- is a page, and the only way to reach
          it was a control that does not read as navigation and never shows as
          current, so a reader who followed any link could not find the way
          back. Aligned with the toggled headings by a spacer, having nothing
          to collapse. */}
      <h3 className="nav-heading">
        <span className="nav-spacer" />
        {/* `end`, or `/` is a prefix of every route and the row is always
            current. */}
        <NavLink to="/" end>
          Overview
        </NavLink>
      </h3>

      {/* First, because it is the part meant to be read rather than looked up
          -- and paired with its position, before the filter, because filtering
          a list renumbers it and the route is the position. */}
      {docs.length > 0 && (
        <NavGroup title="Documentation" href="/documentation">
          {docs.map(({ item, at }) => (
            <NavItem key={at} to={`/documentation/${at}`} label={item.title} />
          ))}
        </NavGroup>
      )}

      <NavGroup title="Endpoints">
        {roots.map((node) => (
          <PathBranch key={node.path} node={node} matches={matches} />
        ))}
      </NavGroup>

      <NavGroup title="Types" href="/types">
        {types.map(({ file, name, value }) => (
          <NavItem key={`${file}/${name}`} to={index.declaration(value)?.href ?? '/types'} label={name} />
        ))}
      </NavGroup>

      {annotationTypes.length > 0 && (
        <NavGroup title="Annotation types" href="/annotation-types">
          {annotationTypes.map(({ file, name, value }) => (
            <NavItem
              key={`${file}/${name}`}
              to={index.declaration(value)?.href ?? '/annotation-types'}
              label={`(${name})`}
            />
          ))}
        </NavGroup>
      )}

      {schemes.length > 0 && (
        <NavGroup title="Security" href="/security">
          {schemes.map(({ file, name, value }) => (
            <NavItem key={`${file}/${name}`} to={index.get(value.id)?.href ?? '/security'} label={name} />
          ))}
        </NavGroup>
      )}

      <div className="sidebar-foot">
        <ThemeToggle />
      </div>
    </nav>
  );
}

/**
 * One path segment: its own row, its methods, and everything under it.
 *
 * Collapsible, like the sections above it. A path with four methods and three
 * child resources is eight rows, and a document has dozens of paths -- without
 * this, reaching the types means scrolling past all of them.
 *
 * A branch with no `endpoint` is a segment nothing declared methods on: the
 * `/books` of a document that only writes `/books/{isbn}`. It is shown, because
 * leaving it out would break the nesting, and it does not link, because there
 * is nothing to show.
 */
function PathBranch({ node, matches }: { node: PathNode; matches: (text: string) => boolean }) {
  const [open, setOpen] = useState(true);
  const relevant = matches(node.path) || node.children.some((child) => within(child, matches));
  if (!relevant) return null;
  const methods = node.endpoint ? methodsOf(node.endpoint) : [];
  const expandable = methods.length > 0 || node.children.length > 0;
  return (
    <li>
      <div className="nav-row">
        {expandable ? (
          <button
            type="button"
            className="nav-toggle"
            aria-expanded={open}
            aria-label={`${open ? 'Collapse' : 'Expand'} ${node.path}`}
            onClick={() => setOpen(!open)}
          >
            <Chevron open={open} />
          </button>
        ) : (
          <span className="nav-spacer" />
        )}
        {node.endpoint ? (
          <NavTo to={`/endpoints/${encodeURIComponent(node.path)}`} label={node.segment} />
        ) : (
          <span className="nav-static">{node.segment}</span>
        )}
      </div>
      {open && methods.length > 0 && (
        <ul className="nav-children">
          {methods.map(([method, operation]) => (
            <li key={method}>
              <NavLink
                to={`/endpoints/${encodeURIComponent(node.path)}/${method}`}
                className={({ isActive }) => `nav-op ${isActive ? 'is-here' : ''}`}
              >
                <Verb method={method} />
                {/* The display name, where there is one. A column of six
                    identical verbs says nothing about which one to open. */}
                {operation.display_name && <span className="nav-op-name">{operation.display_name}</span>}
              </NavLink>
            </li>
          ))}
        </ul>
      )}
      {open && node.children.length > 0 && (
        <ul className="nav-children">
          {node.children.map((child) => (
            <PathBranch key={child.path} node={child} matches={matches} />
          ))}
        </ul>
      )}
    </li>
  );
}

function within(node: PathNode, matches: (text: string) => boolean): boolean {
  return matches(node.path) || node.children.some((child) => within(child, matches));
}

/**
 * One collapsible section of the nav.
 *
 * A document with two hundred types makes every other section unreachable
 * without one, and the heading is the obvious place to put the control. The
 * section title stays a link where it has a page of its own, so collapsing and
 * navigating remain two things.
 */
function NavGroup({ title, href, children }: { title: string; href?: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(true);
  const empty = Array.isArray(children) && children.flat().filter(Boolean).length === 0;
  return (
    <div className="nav-group">
      <h3 className="nav-heading">
        <button
          type="button"
          className="nav-toggle"
          aria-expanded={open}
          aria-label={`${open ? 'Collapse' : 'Expand'} ${title}`}
          onClick={() => setOpen(!open)}
        >
          <Chevron open={open} />
        </button>
        {href ? <NavLink to={href}>{title}</NavLink> : <span>{title}</span>}
      </h3>
      {open && (empty ? <p className="nav-empty">none</p> : <ul>{children}</ul>)}
    </div>
  );
}

/**
 * The link alone, for a row that supplies its own container.
 *
 * Split from `NavItem` because a path branch is already an `<li>` -- it holds
 * its own methods and children -- so wrapping the link in a second one nested
 * `<li>` inside `<li>`. Invalid, and invisible to every check here until
 * `shots.mjs` moved to the dev server: static rendering does not validate
 * nesting, and a production build strips the warning that does.
 */
function NavTo({ to, label }: { to: string; label: string }) {
  const { pathname } = useLocation();
  return (
    <NavLink to={to} className={`nav-item ${decodeURIComponent(pathname) === decodeURIComponent(to) ? 'is-here' : ''}`}>
      {label}
    </NavLink>
  );
}

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <li>
      <NavTo to={to} label={label} />
    </li>
  );
}

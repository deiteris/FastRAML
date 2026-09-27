# viewer

A reference consumer of `fastraml tree` output: a React SPA that renders a RAML
document as API documentation.

It is **not** part of the parser. Nothing in `fastraml/` knows it exists, it has
no build step in the Python gate, and it decides no RAML rule. It is here to be
read alongside the format it consumes — if a construct is awkward to display,
that is evidence about the format, and it has already produced three fixes.

```bash
npm install
npm run sample        # fastraml tree ../fixtures/sample/api.raml > public/api.json
npm run dev
npm run shots         # screenshot every page, both themes, into shots/
npm run shots -- --only=type-object,search --view=phone --light
npm run check         # tsc, layers, smoke
```

`npm run ci` is `check` plus the production build. Neither takes screenshots:
the pictures are for looking at, not for diffing — `shots/` is gitignored — so
capturing them on a runner nobody watches buys a browser download and nothing
else. Run `shots` by hand after a change to layout or to anything client-only.

The document it renders is `fixtures/sample`, which is the repo's and not this
app's: `tests/unit/test_bindings.py` holds `public/api.json` to it, and
`contrib/fastmcp-raml` builds MCP tools while `contrib/raml-mock` runs HTTP
routes from the same file. Editing it moves every consumer `AGENTS.md` lists under `fixtures/`.

## Use the viewer in another frontend

The standalone entry (`src/App.tsx` and `src/main.tsx`) fetches `api.json` by
default and owns a `HashRouter`. To supply the JSON contents directly, render
`<App contents={jsonText} />`; changing `jsonText` replaces the displayed API
without another fetch or page reload. For an application with its own API picker,
render `Viewer` with the selected `fastraml tree` JSON instead. The host supplies
one React Router; **do not nest another router around `Viewer` if the host
already has one**. Viewer links use routes such as `/types` and `/endpoints/…`,
so keep the viewer mounted for those routes (or configure the host router's
basename for a dedicated viewer path).

```tsx
import { MemoryRouter } from 'react-router';
import type { ReactNode } from 'react';
import { Viewer } from './viewer/src/index';

function ApiWorkspace({ contents, picker }: { contents: string; picker: ReactNode }) {
  return (
    <MemoryRouter>
      {picker}
      <Viewer contents={contents} />
    </MemoryRouter>
  );
}
```

`MemoryRouter` keeps this example's viewer routes in memory. Use the host's
existing router instead if it has one; a `HashRouter` or `BrowserRouter` makes
viewer routes shareable. `contents` can come from a backend response or local
state; when it changes, `Viewer` parses the new text and reinitializes its
pages, index and search. Invalid JSON shows an error in the viewer, and a later
valid value recovers it. Parsing uses the exact-number handling in
`src/numbers.ts`, so large example integers are not rounded. You can also pass
an already decoded tree as `<Viewer document={selected} />`. If fetching belongs
to the viewer, use `<LoadedViewer load={stableLoader} />` under the same router;
give it a stable callback, since a changed loader starts a new request. Both
forms leave the host's document title, route-change scrolling and global `/` /
Ctrl+K shortcuts alone.
The standalone `App` opts into those page-wide behaviours with `managePage`.
An embedding host that provides the theme can set `themeToggle={false}` on
`App`, `Viewer`, or `LoadedViewer`: this hides the viewer's theme switch and
ignores its saved theme preference. The host can then set `color-scheme` and
the viewer tokens on `.fastraml-viewer` (the VS Code preview mounts the viewer
in a shadow root and maps inherited theme variables in
`contrib/fastraml-vscode/webview/src/theme.css`).

The host provides its own HTML entry and imports the viewer's package entry.
`Viewer` does not import `main.tsx` or require a `#root` element. Its component
styles are scoped under `.fastraml-viewer` (the normalizer applies to the page),
and the drawer follows its container width;
the host can style its surrounding page independently. For a running example
with a narrow host panel and an API switcher, open `examples/host.html` through
the Vite dev server. It switches JSON text between `Viewer` and `App` and
exercises error recovery. This is source-level integration, not an npm library
build.

## Style

### Reuse and customize the design system

`src/tokens.css` defines the viewer's colours and font stacks. `src/styles.css`
defines its typography, layout, and component styles. The package entry
`src/index.ts` imports `normalize.css` before both stylesheets; if you render
`Viewer` in another app, import the package entry once alongside it. You do
not need the standalone `index.html`.
Styles in `styles.css` are nested under `.fastraml-viewer`, the viewer's root,
so element rules such as `h4`, `button`, and `main` do not style the host app.
`normalize.css` sets browser defaults at page scope; a host needing full CSS
isolation can mount the viewer in a shadow root, as the VS Code preview does.
The standalone page sets its own body margin in `index.html`.

The tokens are CSS custom properties on `.fastraml-viewer`:

| Tokens | Purpose |
|---|---|
| `--bg`, `--panel`, `--raised` | Page, inset surface, and raised control backgrounds |
| `--ink`, `--dim`, `--faint` | Primary, secondary, and quiet text |
| `--line`, `--line-soft` | Container and row dividers |
| `--accent` | Links, selected tabs, and active controls |
| `--sidebar-bg` | Navigation column and narrow top bar; defaults to `--panel` |
| `--nav-hover-bg`, `--nav-hover-ink`, `--nav-selected-bg`, `--nav-selected-ink` | Hovered and current navigation rows; default to the existing divider and text colors |
| `--search-active-bg` | Active search result; defaults to `--line-soft` |
| `--input-bg`, `--input-border` | Search opener surface and border; default to `--bg` and `--line` |
| `--code-bg`, `--inline-code-bg` | Fenced/structured code and inline prose code; default to `--panel` |
| `--focus-border` | Keyboard focus ring on links, buttons, and inputs other than the search dialog field; defaults to `--accent` |
| `--required`, `--warn`, `--recursive`, `--enum` | Semantic markers; keep these distinct from links |
| `--syntax-keyword`, `--syntax-name`, `--syntax-literal`, `--syntax-string`, `--syntax-comment` | Highlighted code |
| `--verb-default`, `--verb-get`, `--verb-post`, `--verb-put`, `--verb-patch`, `--verb-delete` | Method badges; success/info/warning/error status dots share the GET/POST/PUT/DELETE colours |
| `--sans`, `--mono` | Body and code/type/attribute-name font stacks |

Role tokens use `var()` fallbacks to the base palette, so overriding `--panel`
still changes code and the sidebar unless the host gives those roles their own
colors. For example, the VS Code preview maps navigation selection separately
from dividers, and code blocks separately from the sidebar surface. Inside an
`.extra` region, code still uses the page background so it stays distinct from
the enclosing panel.

Light is the default; when the system prefers dark, the viewer uses dark tokens
unless it has an explicit `data-theme`. The switch sets that attribute **on
`.fastraml-viewer`**, not on the document, and saves a preference under
`fastraml-viewer-theme` in local storage. A host can customize either theme
with selectors more specific than the viewer's defaults, loaded after the two
viewer stylesheets. For example, given `<div id="reference"><Viewer document={selected} /></div>`:

```css
#reference .fastraml-viewer {
  --accent: #2447a5;
  --sans: 'Inter', sans-serif;
}

#reference .fastraml-viewer[data-theme='dark'] {
  --accent: #a7c2ff;
}

@media (prefers-color-scheme: dark) {
  #reference .fastraml-viewer:not([data-theme]) {
    --accent: #a7c2ff;
  }
}
```

Change the token values for brand/theme changes, then extend `styles.css` for
structure: `.chip` and its tone classes distinguish markers from muted type
names, `.verb` labels methods, `.tabs` groups alternative panels, `.attributes`
holds rows with one indentation rail per nesting level, and `.prose` handles
rendered Markdown. Use the existing primitives in `src/components/ui.tsx` for
new viewer sections; scope added selectors to `.fastraml-viewer` and use tokens
rather than duplicating colours. The layout uses a 320px sidebar and a drawer
when the viewer's container is at most 720px wide. `styles.css` also holds
narrow styles for headings, rows, tabs, search, and code blocks. Check both
themes at wide and phone widths with `npm run shots`.

The page content is capped at 1000px and centered in the space beside the
sidebar. In a narrower viewer it takes the available width; the sidebar still
becomes a drawer at the container breakpoint.

Measured off `docs.stripe.com`, not eyeballed: `shots.mjs`'s sibling probe loads
the page in the same headless browser and reads computed styles. What came back
and is used here — ink `#1a2c44`, muted `#8c99ad`, page `#f4f7fa` on white,
14px/22px sans, 12px/16px mono, attribute names 14px/700 mono, section headings
with a rule under them, and `+ Show child attributes` as a bordered pill rather
than an underlined phrase.

The lesson that changed the most: **a type name is muted text, not a pill.** An
outlined box per type made a row of facets louder than the attribute name above
it, which is the thing being scanned for. Boxes are kept for markers — a
recursion stop — where stopping the eye is the point.

## Shape of the app

An **operation** is the page, not a resource. A resource with six methods
rendered all six on one screen, so the one being read was one of six full
schemas and the URI parameters that apply to all of them scrolled away. The nav
lists methods under each path, because that is where a name is chosen.

Nesting runs **downward**, indented by one rule per level. A type is a tree and
a table can only grow one way: nesting went into the Type column, so a nested
object pushed its children into a narrowing strip while the page's right half
stayed blank.

Each level has exactly one indentation owner. An explicit nested region (an
expanded reference, an item type, or properties below a visible type heading)
draws it; otherwise an attribute's inline shape does. Attribute lists themselves
never add another rail, which prevents one level from being indented twice.

Alternatives are **tabs** — a union's members, an operation's responses. Each is
a whole thing to read, so stacked they run together and collapsed they have to
be opened one at a time. They are text on a shared baseline with the active one
underlined; a row of filled pills above a rule reads as buttons.

An **example goes above** the attributes it belongs to. It is the fastest way to
understand a type, and last it read as belonging to whichever attribute happened
to come final.

Code blocks are syntax highlighted. Structured values and JSON Schemas are known
to be JSON; string examples and unlabelled Markdown fences use conservative
language detection, while a fence's language label takes precedence.

**Security is a selector, because `securedBy` is a disjunction** — a caller
satisfies any one entry, not all of them. One line of chips read as a single
requirement made of parts, which is the opposite.

Choosing one **changes the page**: the scheme's `describedBy` headers, query
parameters and responses are merged into the operation's own tables, each
borrowed row marked with the scheme it came from. A section of their own pushed
the operation's own parameters below the fold and made a caller assemble one
request from two places. Marked-but-merged is the point — a reader can still see
what would change if the scheme did.

The choice sits on its own row with the type, prose and scopes beneath it.
Trailing them onto the same line wrapped badly with three schemes or more than
two scopes, which is the ordinary case.

A **padlock** marks a secured operation, open where a `securedBy: [null]` entry
means it may also be called unauthenticated.

**Section headings link**: `#/endpoints/…/get#responses` opens the page at its
responses, focused, clear of the phone's sticky bar. Every code block has a
copy button, and the operation's URL has one.

**A part of a page fails alone.** An error boundary around each page, and around
an operation's request and responses, turns an exception into a line saying
which part could not be shown, where it used to blank the app.

An **array says what it holds on its own line** — `tags array of string`. A
label and a rule around one word was three lines of chrome, and for `Book[]` it
repeated what had just been read. An item with structure of its own still gets a
nested block.

Below 720px of viewer width the nav is a **drawer** behind a Menu bar, and a
listing row stacks its description under its name: a fixed 320px column left
a phone a third of its width, and a three-column table broke `object` into
`objec t`. `shots` checks a phone width alongside the split-window 760.

**Search is a dialog, not a filter.** The nav's field used to narrow the tree
by name, which could not find `Book` from a word in its description and could
not say what a match was. The field now opens a dialog, from `/` or Ctrl+K as
well. It searches documentation by title and content; endpoints by path,
display name and description; operations by method and path together
(`get books`) as well as by display name and description; types and
annotation types by name, display name and description; and security schemes
by name, display name, type and description. Results are grouped by what they
are, and the group with the best match comes first: with the groups in the
nav's order, four documentation pages that mention a book in passing pushed
the type named `Book` off screen.

The matching is Fuse.js's token search, with every word required, a name
outranking a display name and a display name outranking prose. `src/search.ts`
holds what Fuse cannot know: what an entry is called and which page shows it,
and its prose as the text a reader sees. A link's target is not searchable,
because it is never shown. The dialog is a modal `<dialog>` holding a
combobox and a grouped listbox; focus returns to whatever opened it. On a phone
it is the whole screen, with a Close button, because there is no Escape key.

Both themes follow the system by default; the toggle has three states, because
one with two silently makes the choice for a reader who never made it.

## Where the document comes from

By default, the standalone app reads `api.json` beside the bundle:

| | for |
|---|---|
| `public/api.json` | a built bundle someone is handed |
| `fastraml serve FILE` | a document being written; the server answers `api.json` with it |

## The contract is generated

`src/tree.d.ts` is written by the TypeScript backend in
`fastraml/views/bindings/`, run from the repository root with `python -m
fastraml.views.bindings typescript -o viewer/src/tree.d.ts`, from the emitter's
own source — **do not edit it**. The key sets come from
`tree.py`'s AST and the facets from the kind classes' annotations, so a facet
added to a kind arrives here without anything being touched by hand.
`tests/unit/test_bindings.py` fails when the checked-in file is stale, and law
19 in `tests/tck/test_properties.py` fails when the corpus emits a key it does
not declare.

The contract names semantic maps and closed vocabularies, and `Shape` is a
discriminated union generated from `KIND_TO_CLASS`: a consumer that narrows to
an object sees object facets rather than every facet of every RAML kind.
Python source inspection lives in the language-neutral `bindings/schema.py`;
the TypeScript backend only renders that schema and supplies TypeScript
spellings for structural values.

`src/model.ts` restates none of it. What lives there is what the JSON does not
carry and a reader needs: which addresses have a page, what to call one, and the
path nesting the model flattened.

## The one rule the renderer follows

Three constructs, and telling the last two apart is the whole discipline
(`docs/16-graph.md` § 6.1):

| | means | what the UI does |
|---|---|---|
| `{"$ref": <address>}` | a link | a name that navigates, and a separate **Show attributes** button that expands in place — **never on render** |
| `{"type": "recursive", …}` | repeats from here | a stop. Its `head` is shown as a name, never as something expandable |
| anything else | containment | descend |

The two controls are visibly different on purpose. A caret glyph beside the
link read as decoration *on* the link, so navigating and expanding looked like
one ambiguous gesture; a word says which is which.

**An expander appears only where there is an attribute list to expand.** "Show
child attributes" has to have child attributes: an array does not, it has an
item type, so `priceHistory: Prices` offered a control that opened onto `each
item → Money` — one more click to reach what the line can say directly. The line
says it: `priceHistory  Prices  Money[]`.

**A description is never behind that control.** It is not an attribute. Where
the type is a reference the prose belongs to the target, and it is shown
without opening the attribute list; the expanded body suppresses it so it
appears once either way.

**A supertype gets the link and no expander.** The projection is unwrapped, so
every attribute a supertype contributes is already in the list below —
expanding it printed the same rows a second time a few pixels from the first.
That is the one reference position where expansion shows nothing new; a
property's `$ref` is not, because `Money`'s attributes are genuinely not
inlined into `Book`.

No ancestor set and no depth budget exist anywhere in `Shape.tsx`, because the
emitter guarantees a cycle is always *marked*. Collapsing the two into a bare
`$ref` would put that bookkeeping back on every consumer, which is the thing
raml2html has no answer to — its `test/outofmemory.raml` is 36 lines.

## Checks

```bash
npm run check     # tsc, the module layering, then render every page
npm run shots     # load every page in a browser, at three widths, both themes
```

Four layers, because each sees what the ones before it cannot:

| | catches |
|---|---|
| `tsc` | types |
| `npm run layers` | a component importing a page, `model.ts`, `load.ts` or `search.ts` importing a component, an import cycle |
| `npm run smoke` | a page that throws or comes back empty; an id twice on one page; a union member named by its container; a `$ref` that resolves nowhere; a search result that opens no page |
| `npm run shots` | anything that only happens in a browser, **and** the layout; the search dialog and section links driven by the keyboard |

`shots` takes what it shoots: `--only=` the page names in `PAGES`, plus the
interactions `search` and `anchor`; `--view=` any of `wide`, `narrow`,
`phone`; `--light` or `--dark`. An unknown name is an error that lists the
known ones. A selective run overwrites what it shoots and leaves the rest of
`shots/`, and skips the check that every nesting construct was seen, since the
pages chosen need not contain them all.

The browser layer is not decoration, though it is not in `check`. `smoke` renders to static markup, which does not
run the client: an icon package that resolved a second copy of React threw
`Invalid hook call` on every page and `smoke` still reported 38/38. `shots`
fails on any console error or uncaught exception, naming the route.

Every visual fault in this app was invisible in the DOM and obvious in a
picture — a caret too small to read as a control, a description printed twice, a
type's own example reading as the last attribute's, two rules where one step of
nesting happened.

`src/smoke.tsx` walks the document's own contents rather than a route list and
renders each page to static markup. `tsc` says the components type-check, which
is not the same as saying they render: an index that misses, a facet whose value
is an object where a string was assumed, and a recursion marker read as a link
all compile. It is the JavaScript half of `TestEveryTypeRenders` in
`tests/tck/test_properties.py`: every declared type and every endpoint has a
view.

It then asks two things rendering cannot answer, because both produce a page
that looks fine and says something untrue:

- **No union member is labelled with the union's own expression.** `type_expr`
  records the expression a shape was *built from*, and P7 builds every member of
  `string | number` from that one node — so each member carries the whole thing.
  Rendered as the member's own name, a two-member union reads `string | number`
  twice. Reintroducing the bug makes this exit 1, which is how it was checked.
- **Every `$ref` resolves in the index** — the JavaScript form of
  `TestEveryReferenceResolves` in `tests/unit/test_tree.py`. One
  that does not renders as `unresolved`, which a reader cannot tell from a
  document that genuinely pointed nowhere.

## Layout

```
src/
  tree.d.ts            GENERATED -- the contract
  walk.ts              GENERATED -- the metamodel and the address index
  model.ts             index, addresses, path nesting, facet spelling
  numbers.ts           JSON parsing that keeps integers a double cannot hold
  load.ts              fetch api.json
  tokens.css           light/dark theme tokens and font stacks
  styles.css           scoped layout and component styles
  search.ts            the search index: entries, Fuse options, prose as text
  App.tsx              standalone api.json loader and hash router
  Viewer.tsx           document/loader integration and optional page behavior
  smoke.tsx            render every page, then the checks rendering cannot make
  pages/               page modules, route table and index.ts exports
  components/
    Shape.tsx          the type renderer -- the traversal law, directly
    Sidebar.tsx        the nav: the title with the theme switch, search button,
                       path tree, declarations, and a footer linking the project
    Search.tsx         the search dialog
    Parameters.tsx, Bodies.tsx, Responses.tsx, Security.tsx, Borrowed.tsx
                       the parts of an operation page
    Url.tsx            base URI and path as one address
    Usages.tsx         what else points at a declaration
    Extra.tsx          annotations and custom facets
    markdown.tsx       descriptions, and why rendering them is safe
    json.tsx, highlighting.ts
                       values and code blocks
    ui.tsx             chips, tabs, sections, linkable headings, copy buttons,
                       error boundaries, the theme switch, icons
layers.mjs             the import layering, asserted
shots.mjs              screenshot every page; fail on console errors and overflow
```

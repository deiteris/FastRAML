# sphinxcontrib-fastraml — RAML API references in Sphinx

A Sphinx extension that makes a RAML 1.0 API part of your documentation. You
can render endpoints, methods, types and security schemes on any page, link to
them from prose the way `:py:class:` links to a class, and fail the build when
the RAML and the docs drift apart.

The RAML is read by `fastraml`. Everything is rendered as native Sphinx content,
not an embedded app, so it is searched, indexed, themed and translated like the
rest of the site, and builds to PDF as well as HTML.

```python
# conf.py
extensions = ['sphinxcontrib.fastraml']
raml_apis = {'books': 'specs/books.raml'}
```

```rst
Books
=====

.. raml:endpoint:: /books
   :depth: -1

Send :raml:method:`POST /books` with a :raml:type:`Book`; the response
carries its :raml:property:`Book.isbn`.
```

## Configuration

`raml_apis` maps a namespace to a root RAML file. Paths are relative to
`conf.py`. Give a `workspace_root` when the RAML reads files above its own
directory, as `fastraml --workspace-root` does:

```python
raml_apis = {
    'books': 'specs/books.raml',
    'users': {'path': 'specs/users/api.raml', 'workspace_root': 'specs'},
}
```

`raml_warn_unrendered` (default `True`) warns about each item of the root
file's namespace that no page renders, and about each declaration the
extension links to but no page renders. Without it, an endpoint added to the
RAML can go undocumented and nothing tells you.

RAML errors and invalid examples are Sphinx warnings at the RAML file and line,
so `sphinx-build -W` fails on a broken specification. Their warning type is
`fastraml.parse`; unrendered items are `fastraml.unrendered`, and an item
rendered twice is `fastraml.duplicate`. Any of these can be silenced through
`suppress_warnings`.

## Directives

Every directive takes `:api:` to choose the API. Without it, the directive uses
the page's `raml:api`, or the only API configured. Every directive also takes
`:no-index:`, which renders without creating link targets.

| Directive | Renders |
|---|---|
| `.. raml:api:: books` | nothing; the rest of the page means this API, as `py:currentmodule` does for Python |
| `.. raml:overview::` | title, version, base URI and its parameters, protocols, media types, default security |
| `.. raml:endpoints::` | every endpoint; `:include:` and `:exclude:` take path regular expressions |
| `.. raml:endpoint:: /books` | one endpoint and its methods |
| `.. raml:method:: GET /books` | one method |
| `.. raml:types::` | the root file's types; `:file:` picks another file |
| `.. raml:type:: Book` | one type, with its properties |
| `.. raml:annotation-types::`, `.. raml:annotation-type:: rateLimit` | annotation types |
| `.. raml:security-schemes::`, `.. raml:security-scheme:: oauth2` | security schemes |
| `.. raml:documentation::` | the RAML's `documentation:` items, as sections in the toctree |
| `.. raml:documentation-item:: Getting started` | one of them, by title |
| `.. raml:send:: POST /books` | a guide's step: what to send, spelled out in place (see *Steps*) |
| `.. raml:expect:: POST /books 201` | a guide's step: what comes back |

**Level of detail.** As with toctree's `:maxdepth:` and `:titlesonly:`:

- `:depth: N` on `raml:endpoint` also renders the endpoints up to N path
  segments below it. `0`, the default, is the endpoint alone; `-1` is
  everything under it.
- `:detail:` on the endpoint, method and declaration directives:
  - `full`, the default, renders everything.
  - `request` leaves out responses: what to send, for a tutorial step.
  - `summary` renders one line per item, each linking to its full entry
    elsewhere. A summary creates no targets. A single method has no summary.
- `:methods: get post` limits which methods an endpoint shows.

**Adding your own text.** The content of a single-item directive is added to
the generated entry, after the RAML's description and before the generated
fields:

```rst
.. raml:method:: POST /books

   Rate-limited to ten requests a minute per token.
```

**Showing an item twice.** Rendering an item on two pages is a warning,
because a link must go to one of them. Put `:no-index:` on the copy, and
links keep going to the reference. For a guide, a step (below) usually reads
better than a copy of the reference entry.

## Steps: what to send, and what comes back

A reference entry lists everything and links outward. A guide's reader needs
the opposite: only what this step takes, spelled out where they are reading.
Two directives write that:

```rst
.. raml:send:: POST /books
   :values:
      tenant = acme
      Authorization = Bearer <your token>
   :body: new-book.json

   Send the whole book as JSON.

.. raml:expect:: POST /books 201
   :body: new-book.json

   The store answers with the book as it stored it.
```

A step renders, in this order:

1. the directive's own text, which is the step's instruction;
2. the method and URL, and which scheme to authenticate with;
3. each input with its meaning and constraints in words: path parameters,
   headers (including those the security scheme adds) and query parameters;
4. for `raml:send`, the body's fields, one level deep, with nested types
   summarised by name. `raml:expect` names the body's type and lists no fields
   by default, because the response block already shows them;
5. the concrete HTTP request or response;
6. one link, to the full reference entry.

A step is never a link target, so it can't compete with the reference.

| Option | Effect |
|---|---|
| `:values:` | `name = value` lines for any input. A value is read as text, else as JSON |
| `:body:` | a JSON file, relative to the page, holding the body |
| `:with:` | optional headers and query parameters to include, by name |
| `:fields:` | which of the body's fields to explain: `none`, `required` or `all`. The default is `required` for `raml:send`, `none` for `raml:expect` |
| `:media:` | which body to show, by media type; by default the first JSON one |
| `:security:` | on `raml:send`, which scheme to authenticate with, or `none` |

**Only validated values are shown.** fastraml validates each value against the
exact shape of the input it's for, and a step uses the first that passes:

1. your own value from `:values:` or `:body:`. If it fails, you get a warning
   with fastraml's reason;
2. the input's own `example`, `examples` or `default`;
3. an example of a declared type the input extends, but only if it validates
   for this input. A body `type: Book` has no example of its own, so it gets
   `Book`'s, unless the body narrows `Book` so that `Book`'s example no longer
   fits.

An example marked `strict: false` is never used. When nothing passes, the
block shows `<name>` for an input, and leaves the body out with a sentence
saying the specification has no example of it. Nothing is made up.

A name in `:values:` or `:with:` that the method doesn't have, or a `:security:`
that doesn't secure it, is a warning.

## Roles

| Role | Target |
|---|---|
| `:raml:endpoint:` | `/books/{isbn}` |
| `:raml:method:` | `GET /books/{isbn}` (the verb in any case) |
| `:raml:response:` | `GET /books/{isbn} 404` |
| `:raml:type:` | `Book`, or `libs/money.raml#Money` |
| `:raml:property:` | `Book.isbn`, or `libs/money.raml#Money.amount` |
| `:raml:annotation-type:` | `rateLimit` |
| `:raml:security-scheme:` | `oauth2` |
| `:raml:documentation-item:` | `Getting started` |
| `:raml:base-uri-parameter:` | `tenant` or `{tenant}` |
| `:raml:api:` | `books`; the link shows the API's title |

Links follow Sphinx's usual rules. ``:raml:type:`the book <Book>` `` gives the
link its own text. A leading `!`, as in ``:raml:method:`!GET /legacy` ``, shows
the name without linking it, for a changelog entry about something removed.
With several APIs, prefix the name with the namespace:
``:raml:method:`books:GET /books` ``.

Value roles write one of the API's values into the prose. Their text is the
API's namespace:

| Role | Writes |
|---|---|
| `:raml:title:` | `title` |
| `:raml:version:` | `version` |
| `:raml:base-uri:` | `baseUri`, as written |
| `:raml:protocols:` | `protocols` |
| `:raml:media-type:` | `mediaType` |

Neither roles nor substitutions work inside a code block, which is where a
base URI is most often wanted.

## What can be named

Only the root file's namespace is addressable: its endpoints, methods,
responses, types and their properties, annotation types, security schemes,
documentation items and base URI parameters.

A declaration in another file, such as a library's type, is named by that file
and not through the root file's `uses:` key. `common.Money` is the root file's
private spelling, and renaming the key would break every link in the prose.
The file is written as every fastraml view names it: relative to the workspace
root, which defaults to the root file's directory. In the ordinary case the
root file is `api.raml` and a library beside it is `common.raml`.

A declaration defined as a typed fragment (`Book: !include book.raml`) is named
in the file that includes it, so it is plain `Book`.

Endpoints nest by path segment, as in the viewer's navigation:
`/books/{isbn}` is one level under `/books` however the RAML declared it.

## How entries read

The RAML is parsed with `unwrap=True` and read as fastraml's effective model:
traits and resource types are applied and inheritance is flattened, so an
entry shows what a caller must actually send.

- **A declared type is linked, never repeated.** A body of `Book` links to
  `Book`'s entry. An inline type is spelled out where it is used. An inline
  subtype of a declared type, such as `type: Book` with a facet added, shows
  only what it changes.
- **Security.** A method lists its security schemes as links. What a scheme's
  `describedBy` adds (headers, query parameters, responses) is in the scheme's
  own entry.
- **Descriptions** are rendered as GitHub-flavoured Markdown. MyST's own syntax,
  such as Sphinx roles, is not understood in them. Other tools read the same
  RAML file, and a role there would show up as literal text in all of them.
  So links go from your prose to the API, never from the API to your prose.

## Not yet

- **Traits and resource types** are neither rendered nor addressable. The
  model keeps their declarations and records which ones each method and
  resource applied, so this is work for the extension, not the parser.
- **`{version}` in the base URI.** RAML fills it in from `version:`, but
  fastraml keeps `baseUri` as written, so this extension shows it as written.
  Filling it in here would be a RAML rule held by a consumer; it belongs in
  fastraml.
- **Applied annotations** are listed with their values, but not rendered
  against their annotation type's shape.

## Example

`examples/bookstore` is a small documentation site for the repository's sample
API (`fixtures/sample/api.raml`), laid out the way a product's docs are:

| Page | Shows |
|---|---|
| `index.rst` | a link to the overview under the API's title, and value roles for its version and base URI |
| `guide.rst` | a tutorial whose steps use `raml:send` and `raml:expect`, with the author's own tenant, token and body (`new-book.json`), all validated |
| `reference/index.rst` | the overview, and a one-line-per-method summary of every endpoint |
| `reference/books.rst` | `/books` and everything below it, with a note of the author's |
| `reference/catalogue.rst` | every other endpoint, picked by a path pattern |
| `reference/types.rst` | the root file's types, then each library's, by file |
| `reference/security.rst` | security schemes and annotation types |
| `reference/documentation.rst` | the specification's own `documentation:` entries, as sections |
| `changelog.rst` | links to what changed, and `!` for what was removed |

Build it with warnings as errors, from this directory:

```bash
uv run sphinx-build -W -b html examples/bookstore examples/bookstore/_build/html
```

Then open `examples/bookstore/_build/html/index.html`. `tests/test_example.py`
builds the same site, so a change that breaks it fails the suite.

## Development

```bash
uv sync --dev
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q
```

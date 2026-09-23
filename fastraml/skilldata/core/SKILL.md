---
name: core
description: Core fastraml usage guide. Read this before running any fastraml command. Covers validating a RAML document, listing what it declares, reading one type or endpoint with inheritance and traits merged in, tracing what uses a type, comparing two versions for breaking changes, exporting the model, and the workspace-root error that catches everyone first. Use when a .raml file is involved and you need to check it for errors, find out what an API declares, work out what a change breaks, or audit a spec. For writing RAML itself, load the raml guide instead.
license: MIT
allowed-tools: Bash(fastraml:*) Read
---

# fastraml core

`fastraml` answers questions about RAML 1.0 API definitions from the command line.

Read the answers from `fastraml`, not from the `.raml` file. A RAML file rarely
holds the whole API: types inherit from other types, traits add headers and
query parameters to operations, resource types add whole methods, and
`!include` pulls in other files and libraries. `fastraml` resolves all of that
first, so it tells you what the API *is* rather than what one file *says*.

Check the install before your first command:

```bash
fastraml --version
```

If that fails, fastraml is not on your PATH. fastRAML is not on PyPI yet, so
install it from GitHub:

```bash
uv tool install git+https://github.com/deiteris/FastRAML
```

Inside a checkout of that repository you can instead prefix every command with
`uv run`, as in `uv run fastraml --version`.

Three features need an extra package. Everything else needs nothing:

- `fastraml query` needs `pyoxigraph`. Without it, the command tells you so and
  exits 1.
- `fastraml serve` needs `fastraml-viewer`, the `fastraml[serve]` extra.
- The `-r` flag, which allows `!include` over HTTP, needs `httpx` or `requests`.

## Other guides

This guide covers the everyday work. Load another when the task calls for it:

```bash
fastraml skills get core --full   # This guide plus the complete flag reference
fastraml skills get raml          # RAML 1.0 itself, for writing or reviewing a file
fastraml skills get lint          # Check style and security; configure or write rules
fastraml skills get backward      # Check backward compatibility; configure the gate
fastraml skills get sparql        # Write your own fastraml query
fastraml skills list              # Everything this version ships
```

## The core loop

```bash
fastraml list -w . api.raml              # 1. See which names the document declares
fastraml show -w . api.raml Book         # 2. Read one, with everything merged in
fastraml refs -w . api.raml Money        # 3. Find every operation that carries it
```

Start with `list` every time. The other commands take a NAME, and `list` is how
you learn which names exist. Every name `list` prints is a name that `show`,
`refs` and `deps` accept, so you never have to guess one.

## Always set the workspace root

`fastraml` only reads files inside one directory. That directory defaults to the
folder holding the file you named, so any `!include` that points to a parent
folder fails:

```
api.raml: invalid
[0]
  file:///.../shared/machine.raml load resource
    path is outside the workspace root: path: .../shared/machine.raml: root: .../sample: suggested_root: ...
hint: the workspace root defaults to the entry file's directory; pass -w ... to widen it
```

This is the most common error you will see, and the document is usually fine.
The root defaults to the entry file's own directory, so any document that
reaches a sibling folder hits it. **The `hint:` line gives you the root to
use** — it is the nearest folder holding both the current root and the file
that was refused. Pass it:

```bash
fastraml validate -w . api/api.raml      # Root is the repo, not api/
```

There is no hint when widening would reach a filesystem or drive root, because
that would hand over every file on the machine.

`-w` works on every command. Set it once and reuse it.

Use `--no-workspace-guard` only on files you trust. It removes the limit
entirely and lets fastraml read any path the process can reach.

## Pick a command

| You want to know | Run |
| --- | --- |
| Is this document valid? | `validate` |
| Is it a *good* document? | `lint` |
| How large is it, and how fast does it parse? | `info` |
| What does it declare? | `list` |
| What does this type or endpoint actually look like? | `show` |
| What breaks if I change this type? | `refs` |
| What is this type built from? | `deps` |
| Did this change break a caller? | `compat` |
| Did this change break a type my library publishes? | `compat --types` |
| Can I have the whole model as data? | `tree` or `graph` |
| What is in here, as a report? | `query` |

## Check a document for errors

```bash
fastraml validate -w . api.raml               # Prints nothing if valid, exits 0
fastraml validate -w . -v api.raml            # api.raml: valid (63.4 ms)
fastraml validate -w . -vv api.raml           # Adds the backend and model counts
fastraml validate -w . a.raml b.raml c.raml   # Checks all three, then exits 1
```

A valid document prints nothing. Errors go to stderr, so `-v` output stays
clean when you pipe it.

`validate` checks every file you give it and exits 1 at the end, rather than
stopping at the first bad one. Run it over a whole directory in CI and you get
every error in one pass.

## See how large a document is

```bash
fastraml info -w . api.raml
```

```
file         api.raml
backend      libyaml
elapsed      61.5 ms
fragments    5
types        51
shapes       133
endpoints    6
annotations  5
```

Use `info` when a parse is slow, or when the same document behaves differently
on two machines. `backend` is the reason for the second case: fastraml uses
`libyaml` when it is available and a slower pure-Python parser when it is not,
and the two do not accept quite the same documents.

## See what a document declares

```bash
fastraml list -w . api.raml                            # Everything
fastraml list -w . api.raml book                       # Names containing "book"
fastraml list -w . api.raml --kind Type --kind Trait   # Repeat --kind to add kinds
```

```
EndPoint         api.raml:467           /books/{isbn}
Operation        api.raml:472           Retrieve a book
Trait            api.raml:419           paged
Type             common.raml:8          Address
```

Each line gives the kind, the file and line it was declared on, and the name.
The pattern match is a substring and ignores case.

The kinds are `Type`, `EndPoint`, `Operation`, `Trait`, `ResourceType` and
`SecurityScheme`. There is no `Annotation` kind: you declare an annotation type
with a type, so it appears as a `Type`.

`list` shows declarations and endpoints only. It does not show the nodes inside
a declaration, such as individual properties, because those outnumber the
declarations roughly twenty to one. Reach those with `deps` instead.

If nothing matches, `list` prints `nothing matching ...` to stderr and exits 1.

## Read one type or endpoint

```bash
fastraml show -w . api.raml Book
```

`show` prints one type, endpoint or operation with everything merged in, as
YAML you can paste back into a document. Each line carries the file and line it
was really written on:

```yaml
Book:                                    # api.raml:153
  type: Entity
  description: One book in the catalogue.
  (deprecated): use Publication instead  # api.raml:165
  properties:
    title:                               # api.raml:176
      type: string
      maxLength: 200
    price:                               # api.raml:184
      type: Money
```

Endpoints benefit most, because that is where RAML hides the most. `show` gives
you the resource type and traits already applied, security after inheritance,
URI parameters from parent resources, and every merged header, query parameter
and body, each tagged with the trait that supplied it:

```bash
fastraml show -w . api.raml '/books/{isbn}'
fastraml show -w . api.raml 'Retrieve a book'   # An operation, by its displayName
```

Nested types are named rather than expanded, which keeps the output to about a
screen. Pass `--depth 2` or more when you need to see inside them.

Traits and resource types have no merged form of their own, because they are
templates that get applied elsewhere. Asking for one is not a mistake: `show`
tells you where it was declared and how many places apply it, then exits 1 and
points you at `refs`.

## Trace what uses a type, or what it uses

`refs` walks outward from a name to everything that can carry it. `deps` walks
the other way, to everything the name is built from.

```bash
fastraml refs -w . api.raml Money --kind Operation   # Rule of thumb: start here
fastraml deps -w . api.raml Book                     # What Book is made of
fastraml refs -w . api.raml Money --depth 3          # Stop after 3 hops
fastraml refs -w . api.raml Money --limit 0          # Print all results
```

Each result is a route, not just a hit, so you can see *why* something matched:

```
Operation  api.raml:472  Retrieve a book -returns-> 200 -payload-> application/json -range-> application/json -property-> price -range-> price -aliasOf-> Money
```

The kind and the file position come first, so you know what was found and where
to go. The route follows.

**Add `--kind Operation` to `refs` almost every time.** Without it you get a
wall of intermediate property nodes. With it you get the list of operations a
change actually reaches, which is the blast radius you wanted.

**Expect a lot of results.** One type in a real API produces thousands of
routes, so `--limit` defaults to 50. fastraml reports the remainder on stderr,
which leaves a piped run untouched. Use `--limit 0` for all of them.

`deps` follows type structure for a type and containment for everything else.
`deps /books` therefore lists the operations on that resource, rather than
reporting that it is built from nothing.

### When a name does not match

```
Usre: no such node
did you mean: User, UserList?
```

fastraml exits 1 and suggests near names, but it never runs one for you. Pick one
and run it yourself.

If two libraries declare the same name, fastraml reports the ambiguity, lists the
full IRI of each candidate, and exits 1. Copy one of those IRIs and pass it back
in place of the bare name.

## Compare two versions

```bash
fastraml compat --no-workspace-guard old/api.raml new/api.raml
```

```
# API compatibility

## `GET /books`

### Response

**Removed**

| Where | Path | Type | Compatibility |
|---|---|---|---|
| `200` body `application/json` | `$.discount` | optional `number` | Breaking |
```

`compat` exits 1 when any change is breaking, so it gates CI without you parsing
the output. Results are grouped by operation, then by side of the wire (Request
for what a caller sends, Response for what it receives), then by kind: Removed,
Changed or Added. Changes the API root makes for every operation, added and
removed operations, and edits that reach several operations each appear once,
above the per-operation sections. Each report opens with a legend for its
sections.

Omit `-w` when each version is self-contained below its own folder. Use one
common `-w` when both versions intentionally share a trusted workspace, or
`--no-workspace-guard` for trusted documents whose includes need unrestricted
access. Workspace roots control file access; compatibility does not compare
source-file addresses.

Narrow the output with `--breaking-only`, or with `--severity`, which is a
threshold: it shows that impact and everything worse, so `--severity review`
hides compatible and cosmetic rows. Neither flag changes the exit code. Use
`--json` when a program reads the result. For CI, impact policy, JSON fields and
project overrides, run:

```bash
fastraml skills get backward
```

## Export the whole model

```bash
fastraml tree -w . api.raml                  # The whole document as JSON
fastraml tree -w . api.raml --positions      # Source span of every declaration
fastraml graph -w . api.raml                 # Turtle (the default)
fastraml graph -w . api.raml --format json   # Nodes and edges as JSON
fastraml graph -w . api.raml --format dot    # Graphviz
fastraml graph -w . api.raml --format nt     # N-Triples
fastraml openapi -w . api.raml               # OpenAPI 3.0.3 YAML
fastraml openapi -w . api.raml --format json # OpenAPI 3.0.3 JSON
fastraml openapi -w . api.raml -o api.yaml   # To a file, UTF-8 with LF newlines
fastraml serve -w . api.raml                 # In a browser (needs fastraml-viewer)
```

`tree`, `graph` and `openapi` feed another tool. `serve` is for a person to
read: it shows the `tree` output in a browser, on loopback by default, and exits
1 naming the package when `fastraml-viewer` is missing. Choose `tree` when you
need the contents, because it inlines examples, defaults and every container.
Choose `graph` when you need identity and references, which is what it carries
instead.

Both assign the same addresses to the same nodes, so an address from one names
the same thing in the other.

**Write these to a file with `-o FILE`, never a shell redirect.** `graph`,
`openapi`, `tree`, `query` and `compat` all take it, and the file is UTF-8 with
LF newlines whatever shell or platform ran the command. A redirect on Windows
writes CRLF, so output you commit stops matching what regenerates it. On
`compat` it matters twice over: that command exits 1 whenever something is
breaking, so a redirect cannot tell you whether the report was written.

`openapi` is the RAML 1.0 to OpenAPI 3.0.3 conversion. A type declared once is
exported once, under `components/schemas`, and referenced with `$ref` wherever
it is used — including types from libraries and from `!include`d JSON Schema
files. Things the target format cannot carry are reported as `warning:`
lines on stderr and the exit code stays 0, so read the warnings and fix the
spots they name.

## Audit a whole document

Two commands, and which one you want depends on whether the answer has a
*severity*.

**`fastraml lint` reports defects.** Unused types, operations left unsecured,
strings with no upper bound, a property that is both optional and nilable. Each
finding names a rule, carries a severity, and can fail CI:

```bash
fastraml lint -w . api.raml --format text
fastraml lint -w . api.raml --format text --rule explicit-uri-parameter
fastraml lint --list-rules                    # what is available
fastraml lint --explain unused-type           # one rule, with good and bad RAML
```

Only the `spec` rules run by default. The `security`, `http`,
`problem-details`, `i-json` and `style` sets are opt-in, and house rules come
from a plugin; both are enabled in a config file:

```bash
fastraml skills get lint
```

**`fastraml query` reports facts.** Questions with no right answer and nothing
to fail on — the table of contents, which media types are in use, every enum:

```bash
fastraml query --list                            # See all nine
fastraml query --show endpoint-tree              # Read one before running it
fastraml query -w . api.raml -n endpoint-tree    # Run it
fastraml query -w . api.raml -n type-fan-in
```

The ones you will reach for most: `endpoint-tree`, `type-fan-in`,
`error-response-types`, `media-types` and `scheme-usage`.

`query` runs SPARQL and needs `pyoxigraph`, but `--list` and `--show` work
without it and without a document, so you can always read what a query would
ask.

For a question about one *name*, `refs`, `deps` and `show` are faster than
either and give you routes, which SPARQL cannot. To write your own query:

```bash
fastraml skills get sparql
```

## Exit codes

- `0` — the document is valid, or the command produced its answer.
- `1` — the document is invalid, the name did not resolve, the name was
  ambiguous, nothing matched, `compat` found a breaking change, `lint` found a
  finding at `error` severity, or an optional package is missing.
- `2` — the command line itself was wrong, such as an unknown command or a
  missing argument.

`1` often means "no results" rather than "something failed". fastraml always
writes the reason to stderr, so read it before you treat it as a failure.

## Troubleshooting

- **`outside workspace root`** — set `-w` to a folder containing every file the
  document reaches. See [Always set the workspace root](#always-set-the-workspace-root).
- **`no such node` for a name you can see in the file** — fastraml resolves names
  against the merged model. Run `list` and use a name it prints.
- **`--remote needs an HTTP client`** — install `httpx` or `requests`, or drop
  the `-r` flag.
- **`query needs an RDF store`** — install `pyoxigraph`, or answer the question
  with `list` and `refs` instead.
- **`show` finds nothing in a file that clearly has content** — the file is
  probably a library or a fragment rather than an API. Run `list` to see what it
  declares.
- **Git Bash on Windows turns `/books/{isbn}` into a Windows path** — quoting
  does not stop this. Prefix the command with `MSYS_NO_PATHCONV=1`.
- **`BrokenPipeError` from `tree` or `graph`** — you piped the output into
  something that stopped reading, such as `head`. The document is fine.

## What fastraml does not do

- It reads RAML 1.0 only. It does not read OpenAPI or Swagger.
- It does not implement Overlays or Extensions, and it does not support XML
  Schema external types. JSON Schema external types do work.
- Only `validate` and `info` check a document for errors. The other commands
  turn validation off on purpose, because a document with a bad example still
  has a model worth reading. A clean `list` therefore does not mean a valid
  document. Run `validate` when you need that answer.

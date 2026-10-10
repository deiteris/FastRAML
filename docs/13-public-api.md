# 13 - Public API

The public API is not stable before 1.0. Consumers should pin a compatible
release range. The authoritative top-level surface is `fastraml.__all__`; it is
implemented by the lazy export table and declared eagerly in
`fastraml/__init__.pyi`. Do not infer support from an importable internal module.

## 1. Entry points

```python
from fastraml import ParseOptions, parse_from_path

raml = parse_from_path('api.raml', ParseOptions(unwrap=True, validate=True))
api = raml.entry_point
```

```python
def parse_from_path(path: str | os.PathLike[str], options: ParseOptions | None = None) -> Raml: ...

def parse_from_string(
    content: str,
    *,
    file_name: str,
    base_dir: str | os.PathLike[str],
    options: ParseOptions | None = None,
) -> Raml: ...

def parse_lenient(
    path: str | os.PathLike[str], options: ParseOptions | None = None
) -> tuple[Raml, RamlError | None]: ...
```

`parse_from_path` resolves a relative entry path against the current directory.
`parse_from_string` requires an absolute `base_dir`, because relative includes
need a real filesystem base. Strict entry points raise `RamlError`.

`parse_lenient` runs the same pipeline and returns a partial model with a
nonfatal accumulated error. Safe local boundaries recover unknown API/Library
fields, unknown endpoint fields, security definitions, security and annotation
binding, and the discriminator inline-declaration check (docs/11 § 2).
Prerequisite failures stop at the pass where strict parsing stops; later passes
do not run against their incomplete prerequisites. An entry load failure always
raises. During parsing, an unknown or unsupported fragment kind, a
fragment-kind mismatch, a non-mapping root, or an `extends` chain that cannot
be loaded (`extends is required`, `extends must be a string`, `resolve
extends`) raises only when the outer frame's location is the entry URI; the
same failure in an included fragment is returned with the partial model.

A returned model says how far it got. `Raml.completed` lists the stages that
finished, in order, and `Raml.stopped_at` names the stage that stopped parsing, or
is `None` (docs/02 § 1). Locally recovered errors can leave all requested
stages completed and `stopped_at=None` with a nonempty returned error. Gate a
feature on membership, as in
`Stage.RESOLVED in raml.completed`, never on a later stage having run: P9 is
optional, so `VALIDATED` can finish without `UNWRAPPED`. A stage that did not
run leaves its outputs at their defaults; for example, `endpoints` is empty
until `ENDPOINTS` finishes, which is not the same as an API with no
resources.

A stage can hold more than one step, and `stopped_at` names the stage, not
the step. `RESOLVED` is P7 and then the discriminator declaration check
(docs/05 § 6), so a model that check stopped has `stopped_at` set to
`RESOLVED` although P7 finished: invariant I5 may hold there without
`RESOLVED` in `completed`.

When `completed` is empty, `entry_point` may be `None` or an empty shell. An
entry document whose own YAML does not compose leaves the fragment it
registered, with nothing decoded, when it is an API, a Library or another
fragment, and `None` when it is an Overlay or an Extension, whose chain was
never loaded.

`Raml.broken` maps an entity's id to the `RamlError` that left it incomplete.
A marked entity is in the model, and its identity (name, `key_pos`,
`value_pos`, `location`) is sound; its content is partial. Read a marked
entity as "present, not whole": show it, and do not treat its content as
complete. The invariants of docs/02 § 4 hold for every entity that is not
marked. An entity is marked if its own content failed or if the failure
passed through it from something it contains, so a marked entity may hold
sound and marked children. The mark carries that entity's chain; the
returned error includes locally recovered failures and any later failure.
What is kept and marked today:

| Entity | Kept as |
|---|---|
| A type or annotation type declaration, or a DataType fragment's root | Its kind, and each property, `items` or `anyOf` member that built; the failed one is absent, and the other facets are not decoded. A failure before its kind was settled leaves an `UnknownShape`, never `shape is None` |
| A trait, resource type or security scheme definition | Every key that decoded; a security scheme's `describedBy` and its responses as a resource's. An unloadable `!include` has `link is None`; a loaded definition fragment keeps its partial link |
| A resource, an operation, a response | Every key that decoded, and every child, sound or marked |
| A nested resource whose full URI an earlier resource took, and each resource enclosing it | In its parent's `endpoints`, but not in `Raml.endpoints`. A top-level one is absent, with every resource beneath it |
| An operation a trait failed to apply to, and a resource a resource type failed to apply to; each resource enclosing either | Everything but that template's contribution. The `DirectiveRef` stays in `traits` or `resource_type`, with `resolved is None` if the name matched nothing |
| A `uses:` entry whose library failed | The library as it stands, if it loaded; otherwise `link is None` |
| A type declaration, `examples:` holder or definition whose `!include` finds a fragment that failed on an earlier include | Linked to the fragment as it stands; nothing is reported again |
| A `securedBy:` entry whose scheme did not bind, or whose parameters failed (P5) | `definition is None` if the name bound nothing; a scheme that bound keeps `definition` when its `scopes` failed |
| A `securedBy:` entry naming a broken security definition in a lenient parse | Its partial `definition`; application parameters are not compiled |
| A shape whose kind P7 could not settle, and each shape the failure passed through | An `UnknownShape`; one whose kind P7 settled but whose declaration facets failed keeps its kind, as a declaration does |
| An annotation application whose type P8 could not find | `defined_by is None` |
| A shape whose merge P9 rejected, and each shape enclosing or inheriting from it | Its declared, unmerged form, not flagged unwrapped (docs/07 § 6) |

Anything else that fails to build is absent (docs/11 § 2). A failure in a
check that builds nothing, the discriminator declaration check or P10, marks
nothing: the entity it names is whole, and only the returned error reports
it. On success, `broken` is empty. `tests/partial/`, run with
`pytest --mutations`, explores this contract over mutations of every valid
TCK document and of the fixtures (docs/14 § 3).

An Overlay or Extension may be the entry document. The returned `Raml` holds
the target tree of its `extends` chain: `entry_point` is the root API's
`APIFragment`, `Raml.location` is the root API's URI, and `Raml.extensions`
lists the applied documents in order ([19](19-overlays-and-extensions.md)
§ 6). The default workspace root is the entry document's directory, so a chain
that reaches above it (`extends: ../api.raml`) needs `workspace_root`.

`fastraml.join.join(paths, JoinOptions(...))` combines API documents and
returns RAML text ([20](20-join.md) § 8). It is not part of `fastraml.__all__`.

## 2. Parse options

| Option | Effect |
|---|---|
| `unwrap` | Flatten type inheritance and mark recursive shapes. |
| `validate` | Check declarations and validate examples, defaults, enums, custom facets, and annotations. It privately unwraps copies when `unwrap` is false; they are not added to `Raml.shapes` (docs/10 § 1). |
| `retain_source` | Retain source nodes, texts, and shape source information for source-aware consumers, and keep `Raml.include_nodes`, which is otherwise emptied when the parse ends ([03](03-yaml-and-io.md) § 4.3). |
| `retain_text` | Retain each file's text only, which the occurrence index needs ([16](16-graph.md) § 9). Implied by `retain_source`. |
| `workspace_root` | Set the file-access sandbox root and the base for RAML-absolute includes. Defaults to the entry file directory. |
| `max_include_size` | Limit each `!include` target; zero disables the limit. |
| `file_loader` | Replace the `file://` loader. The caller then owns filesystem safety. |
| `http_client` | Enable HTTP(S) includes with a synchronous client. |
| `regex_engine` | Select `re` or optional `re2` for fastRAML-compiled regexes. |
| `max_depth` | Set the shared input-recursion ceiling. |

Pass `unwrap=True, validate=True` together unless you specifically need declared,
unflattened types. Validation alone must clone and unwrap declarations privately.

Default lint no longer needs whole trees merely to detect `schema:` and
`schemas:`: the decoders record their accepted spelling and position in
`Raml.syntax_aliases` (docs/04 § 5, docs/05 § 3), independent of source
retention. A consumer needing only that compact fact should read it rather
than retaining the whole source tree.

A configuration file's `parser:` section is `ParserConfig`. `limits(options)`
applies its `max_include_size`, `max_depth` and `regex_engine`; its
`workspace_root` and `remote` are left to the host, which weighs them against
its own flags and folders.

## 3. Model contracts

One `Raml` holds the result of one parse. Its public stores include the entry
fragment, fragments by URI, the applied Overlays and Extensions, shapes in creation order, endpoints by full URI,
domain extensions, include references, and retained source data when requested.
Use its `types_in`, `annotation_types_in`, `typedefs_in`, `include_refs_in`, and
`source_node` accessors for indexed lookups.

Consumers must honor these contracts:

1. The model may be cyclic. Track visited identities in arbitrary traversals.
2. The model is mutable and unguarded. Mutating parser-owned objects invalidates
   parser invariants.
3. A `Raml` instance is single-threaded while parsing and validating. Read-only
   traversal after completion is safe; do not concurrently validate one shape.
4. Without unwrap, a shape exposes only its own declaration. Direct
   `BaseShape.validate()` and `validate_or_raise()` require an unwrapped shape.
5. A shape's `type_expr` is a `WrittenScalar` in a returned model, strict or
   partial: the expression's `value` and `position`, not a YAML `Node`. With
   `retain_source` it stays the `Node`. Read only `value` and `position`
   (docs/05 § 1).

Parsing, `build_graph`, `build_occurrences`, `Linter` runs, `to_openapi`, and
the `lsp` verb raise the garbage collector's full-collection threshold while
they run and restore it afterwards ([12](12-performance.md) § 6); `lsp` holds
it for the server's whole run. Thresholds are process-wide, so other threads
also skip full collections during that time. Young collections run as usual.
`set_gc_tuning(False)` turns this off for the whole process:

```python
import fastraml

fastraml.set_gc_tuning(False)
```

A JSON Schema type's RAML reading, `JsonShape.as_shape()`, is `None` where the
schema has none, and `JsonShape.projection_error()` returns the `RamlError` that
says why; neither raises (docs/10 § 7). `projected(base)` then returns `base`.

For direct value validation, `validate(value)` returns `None` or `RamlError`.
`validate_or_raise(value)` raises the same error. Use concrete exported shape
classes with `isinstance` for type narrowing.

## 4. Exports and typing

`fastraml` re-exports parser entry points, options, diagnostics, loaders, model
classes, concrete shapes, common YAML/data records, configuration records, and
the supported view and backward-compatibility interfaces. The exact current list
is `fastraml.__all__`; it is deliberately lazy at runtime and mirrored by the
stub file for type checkers.

The package ships `py.typed`. Public code is type checked with strict mypy. A
consumer that needs a name not in `__all__` should treat it as internal until the
package exports it deliberately.

## 5. CLI

`fastraml` provides these commands:

| Command | Purpose |
|---|---|
| `validate FILE...` | Parse, unwrap, and validate files. `--json` writes JSON Lines. |
| `info FILE` | Print backend, timing, and model counts. |
| `graph FILE` | Export the effective graph as Turtle, N-Triples, DOT, or JSON. |
| `convert openapi FILE.raml` | Export an effective API as OpenAPI 3.0.3 YAML or JSON. |
| `convert jsonschema FILE.raml [TYPE]` | Export a DataType fragment or one named API/Library type as JSON Schema draft-07. |
| `convert raml FILE.json` | Export JSON Schema as a RAML DataType or Library. |
| `tree FILE` | Export the effective addressed tree, or positions with `--positions`. |
| `serve FILE` | Serve the tree through `fastraml-viewer`. |
| `list FILE [PATTERN]` | List nameable declarations, endpoints, and operations. |
| `refs FILE NAME` | Walk incoming graph routes. `--sites` prints where the name is written instead, as `FILE:LINE:COLUMN`, from the occurrence index ([16](16-graph.md) § 9). |
| `deps FILE NAME` | Walk outgoing graph routes. |
| `show FILE NAME` | Render an effective type, endpoint, or operation. |
| `compat OLD NEW` | Compare effective APIs, or `types:` with `--types`. |
| `join INPUT INPUT...` | Combine API documents into one RAML document ([20](20-join.md)). |
| `query` | List/show named SPARQL queries or run `-n`, `-q`, or `-Q` against a file. |
| `lint [FILE...]` | Run configured lint rules, list rules, or explain one rule; `--fail-on` selects the exit threshold. |
| `skills` | List, print, or install packaged agent-guide stubs. |
| `lsp` | Serve LSP over stdio for the editor's workspace folders ([21](21-language-service.md) § 5). |

All parsing commands except `skills` and `lsp` accept the common configuration
and workspace options; `lsp` takes `--config` and `-r`, and its sandbox is the
editor's folders. CLI parsing always unwraps. `validate` and `info` validate;
reading and view commands parse without validation so a partially invalid
document remains navigable. Diagnostics use stderr; document output uses
stdout, or `-o FILE` where offered, with UTF-8 and LF newlines. `query` needs
`fastraml[graph]`; `serve` needs `fastraml[serve]`; `lsp` needs `fastraml[lsp]`; `-r` needs an
HTTP client such as `fastraml[http]`. Lint defaults to failing on `error`;
`--fail-on warning` also fails on warnings.
The CLI's `-r` client follows HTTP redirects; a library caller supplying its own
`http_client` controls that client's redirect policy.

The CLI is the package `fastraml/cli/`; `python -m fastraml.cli` runs the same
entry point as the console script. `arguments.py` builds the argument parser
and names each verb's handler as `module:function` text, so `--help` and
`--version` import no verb module, parser, view, or YAML library. `common.py`
holds what several verbs share: parse options, parsing with a failure report,
and writing a document. Each other module is one verb group, and imports its
heavy and optional dependencies inside the verb.

For library callers, `to_raml(shape, *, name=None)` accepts a compiled
`JsonShape` and returns the same complete RAML document (docs/16 § 8). It raises
the schema's projection error where the schema has no projection.

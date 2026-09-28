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
nonfatal accumulated error. It stops at the pass where strict parsing stops; it
does not run later passes against incomplete prerequisites. An entry load failure
always raises. During parsing, an unknown or unsupported fragment kind, a
fragment-kind mismatch, or a non-mapping root raises only when the outer frame's
location is the entry URI; the same failure in an included fragment is returned
with the partial model.

A returned model says how far it got. `Raml.completed` lists the stages that
finished, in order, and `Raml.stopped_at` names the stage that raised, or is
`None` (docs/02 § 1). Gate a feature on membership, as in
`Stage.RESOLVED in raml.completed`, never on a later stage having run: P9 is
optional, so `VALIDATED` can finish without `UNWRAPPED`. A stage that did not
run leaves its outputs at their defaults; for example, `endpoints` is empty
until `ENDPOINTS` finishes, which is not the same as an API with no
resources.

`Raml.broken` maps an entity's id to the `RamlError` that left it incomplete.
A marked entity is in the model, and its identity (name, `key_pos`,
`value_pos`, `location`) is sound; its content is partial. Read a marked
entity as "present, not whole": show it, and do not treat its content as
complete. The invariants of docs/02 § 4 hold for every entity that is not
marked. An entity is marked if its own content failed or if the failure
passed through it from something it contains, so a marked entity may hold
sound and marked children. The mark carries that entity's chain; the
returned error is still the one a strict parse raises. What is kept and
marked today:

| Entity | Kept as |
|---|---|
| A type or annotation type declaration | Its kind, and each property, `items` or `anyOf` member that built; the failed one is absent, and the other facets are not decoded. A failure before its kind was settled leaves an `UnknownShape`, never `shape is None` |
| A trait, resource type or security scheme definition | Every key that decoded; a security scheme's `describedBy` and its responses as a resource's. One whose `!include` failed has `link is None` |
| A resource, an operation, a response | Every key that decoded, and every child, sound or marked |
| An operation a trait failed to apply to, and a resource a resource type failed to apply to; each resource enclosing either | Everything but that template's contribution. The `DirectiveRef` stays in `traits` or `resource_type`, with `resolved is None` if the name matched nothing |
| A `securedBy:` entry whose scheme did not bind (P5) | `definition is None` |
| A shape whose kind P7 could not settle, and each shape the failure passed through | An `UnknownShape`; one whose kind P7 settled but whose declaration facets failed keeps its kind, as a declaration does |
| An annotation application whose type P8 could not find | `defined_by is None` |
| A shape whose merge P9 rejected, and each shape enclosing it | Its declared, unmerged form, not flagged unwrapped (docs/07 § 6) |

Anything else that fails is absent (docs/11 § 2). On success, `broken` is
empty. `tests/partial/`, run with `pytest --mutations`, explores this
contract over mutations of every valid TCK document and of the fixtures
(docs/14 § 3).

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
| `validate` | Check declarations and validate examples, defaults, enums, custom facets, and annotations. It privately unwraps copies when `unwrap` is false. |
| `retain_source` | Retain source nodes, texts, and shape source information for source-aware consumers. |
| `retain_text` | Retain each file's text only, which the occurrence index needs ([16](16-graph.md) § 9). Implied by `retain_source`. |
| `workspace_root` | Set the file-access sandbox root and the base for RAML-absolute includes. Defaults to the entry file directory. |
| `max_include_size` | Limit each `!include` target; zero disables the limit. |
| `file_loader` | Replace the `file://` loader. The caller then owns filesystem safety. |
| `http_client` | Enable HTTP(S) includes with a synchronous client. |
| `regex_engine` | Select `re` or optional `re2` for fastRAML-compiled regexes. |
| `max_depth` | Set the shared input-recursion ceiling. |

Pass `unwrap=True, validate=True` together unless you specifically need declared,
unflattened types. Validation alone must clone and unwrap declarations privately.

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

Parsing, `build_graph`, `Linter` runs, and `to_openapi` raise the garbage
collector's full-collection threshold while they run and restore it afterwards
([12](12-performance.md) § 6). Thresholds are process-wide, so other threads
also skip full collections during that time. Young collections run as usual.
`set_gc_tuning(False)` turns this off for the whole process:

```python
import fastraml

fastraml.set_gc_tuning(False)
```

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
document remains navigable. Diagnostics use stderr; document output uses stdout
or `-o FILE` with UTF-8 and LF newlines. `query` needs `fastraml[graph]`;
`serve` needs `fastraml[serve]`; `lsp` needs `fastraml[lsp]`; `-r` needs an
HTTP client such as `fastraml[http]`. Lint defaults to failing on `error`;
`--fail-on warning` also fails on warnings.

For library callers, `to_raml(json_shape, name=None)` accepts a compiled
`JsonShape` and returns the same complete RAML document (docs/16 § 8).

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

## 2. Parse options

| Option | Effect |
|---|---|
| `unwrap` | Flatten type inheritance and mark recursive shapes. |
| `validate` | Check declarations and validate examples, defaults, enums, custom facets, and annotations. It privately unwraps copies when `unwrap` is false. |
| `retain_source` | Retain source nodes, texts, and shape source information for source-aware consumers. |
| `workspace_root` | Set the file-access sandbox root and the base for RAML-absolute includes. Defaults to the entry file directory. |
| `max_include_size` | Limit each `!include` target; zero disables the limit. |
| `file_loader` | Replace the `file://` loader. The caller then owns filesystem safety. |
| `http_client` | Enable HTTP(S) includes with a synchronous client. |
| `regex_engine` | Select `re` or optional `re2` for fastRAML-compiled regexes. |
| `max_depth` | Set the shared input-recursion ceiling. |

Pass `unwrap=True, validate=True` together unless you specifically need declared,
unflattened types. Validation alone must clone and unwrap declarations privately.

## 3. Model contracts

One `Raml` holds the result of one parse. Its public stores include the entry
fragment, fragments by URI, shapes in creation order, endpoints by full URI,
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
| `openapi FILE` | Export an effective API as OpenAPI 3.0.3 YAML or JSON. |
| `tree FILE` | Export the effective addressed tree, or positions with `--positions`. |
| `serve FILE` | Serve the tree through `fastraml-viewer`. |
| `list FILE [PATTERN]` | List nameable declarations, endpoints, and operations. |
| `refs FILE NAME` | Walk incoming graph routes. |
| `deps FILE NAME` | Walk outgoing graph routes. |
| `show FILE NAME` | Render an effective type, endpoint, or operation. |
| `compat OLD NEW` | Compare effective APIs, or `types:` with `--types`. |
| `query` | List/show named SPARQL queries or run `-n`, `-q`, or `-Q` against a file. |
| `lint [FILE...]` | Run configured lint rules, list rules, or explain one rule; `--fail-on` selects the exit threshold. |
| `skills` | List, print, or install packaged agent-guide stubs. |

All parsing commands except `skills` accept the common configuration and workspace
options. CLI parsing always unwraps. `validate` and `info` validate; reading and
view commands parse without validation so a partially invalid document remains
navigable. Diagnostics use stderr; document output uses stdout or `-o FILE` with
UTF-8 and LF newlines. `query` needs `fastraml[graph]`; `serve` needs
`fastraml[serve]`; `-r` needs an HTTP client such as `fastraml[http]`. Lint
defaults to failing on `error`; `--fail-on warning` also fails on warnings.

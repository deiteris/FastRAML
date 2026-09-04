# 13 — Public API

The surface a consumer sees. Everything not listed here is private and may change;
the package exports exactly this and marks the rest with a leading underscore or
keeps it out of `__init__`.

## 1. Entry points

```python
from pyraml import parse_from_path, parse_from_string, ParseOptions

raml = parse_from_path("api.raml", ParseOptions(validate=True, unwrap=True))
api = raml.entry_point  # APIFragment | Library | DataTypeFragment | …
```

```python
def parse_from_path(path: str | os.PathLike[str], options: ParseOptions | None = None) -> Raml: ...


def parse_from_string(
    content: str, *, file_name: str, base_dir: str | os.PathLike[str], options: ParseOptions | None = None
) -> Raml: ...
```

- `parse_from_path` resolves a relative path against the CWD and defaults the
  workspace root to the file's directory.
- `parse_from_string` requires an **absolute** `base_dir`, because relative
  `!include`s have to resolve against something real.
- Both raise `RamlError` on failure. There is no "errors" return value; a caller
  that wants partial results on failure uses `parse_lenient` (below).

```python
def parse_lenient(...) -> tuple[Raml, RamlError | None]: ...
```

Same work, but returns the partial model alongside the accumulated error instead
of raising. This is what an editor integration uses; it is a thin wrapper, not a
second implementation. It lands with validation, in Phase 8: until every pass
accumulates there is little partial model to hand back.

## 2. Options

```python
@dataclass(frozen=True, slots=True)
class ParseOptions:
    unwrap: bool = False
    validate: bool = False
    retain_source: bool = False
    workspace_root: str | os.PathLike[str] | None = None
    max_include_size: int = 65536  # 0 disables
    file_loader: ResourceLoader | None = None
    http_client: Any | None = None  # enables http(s) includes
    regex_engine: Literal["re", "re2"] = "re"
    max_depth: int = 200
```

| Option | Effect |
|--------|--------|
| `unwrap` | run P9: flatten inheritance in place. The model then shows each type complete; `inherits` still records the chain. |
| `validate` | run P10: `check()` every declaration, validate examples/defaults/enums/annotations. Implies an unwrap of a **copy** when `unwrap=False`. |
| `retain_source` | keep raw node trees (`raml.source_node(uri)`) and the entity→node index (`raml.source_info`). Off by default; costs memory. |
| `workspace_root` | widen the I/O sandbox and the base for RAML-absolute includes. Default: the entry file's directory. |
| `max_include_size` | per-file cap for `!include` targets. |
| `file_loader` | replace the `file://` loader (e.g. to shadow unsaved buffers). **Disables the built-in sandbox** — see [03](03-yaml-and-io.md) § 5. |
| `http_client` | supply a client to enable remote includes. Absent ⇒ `http(s)` URIs are rejected. |
| `regex_engine` | `"re"` (default, ECMA-ish, backtracking) or `"re2"` (linear time; requires `google-re2`). Use `"re2"` for untrusted input — noting that it covers every regex pyRAML compiles but **not** the ones executed inside an external JSON Schema ([01](01-scope-and-coverage.md) D3). |
| `max_depth` | one ceiling for **every** recursive descent bounded only by the input — document conversion, unwrap, recursion-marking and the JSON Schema walks. They defend the same C stack ([12](12-performance.md) § 14). |

**Recommendation, stated in the docstring:** pass `unwrap=True, validate=True`
together unless you specifically need to inspect un-flattened declarations.
`validate=True` alone performs a private copy-and-unwrap per type.

## 3. `Raml`

```python
class Raml:
    entry_point: Fragment | None
    fragments: dict[str, Fragment]  # by URI
    shapes: list[BaseShape]  # in creation order
    domain_extensions: list[DomainExtension]
    endpoints: dict[str, EndPoint]  # by full URI
    include_refs: dict[str, list[IncludeRef]]

    location: str  # the entry point's, or '' before one is set
    is_unwrapped: bool

    def types_in(self, uri: str) -> Mapping[str, BaseShape]: ...
    def annotation_types_in(self, uri: str) -> Mapping[str, BaseShape]: ...
    def typedefs_in(self, uri: str) -> Sequence[BaseShape]: ...
    def include_refs_in(self, uri: str) -> Sequence[IncludeRef]: ...
    def source_node(self, uri: str) -> Node | None: ...  # retain_source only
```

The stores named in [02](02-architecture.md) § 3 *are* the read surface. A method
cannot share its name with the slot that holds the data, and a method that copied
the dict on every call would buy nothing: § 7 already tells a consumer not to
mutate what it is handed.

The `*_in(uri)` accessors exist for the indices keyed twice over — by file URI,
then by declaration name. Each returns an empty mapping for a file that declared
nothing, rather than raising or returning `None`.

## 4. The model

Exported model classes, all read-oriented, all slotted, all carrying
`location`/`key_pos`/`value_pos`:

**Fragments** — `APIFragment`, `Library`, `DataTypeFragment`, `NamedExample`,
`DocumentationItemFragment`, `TraitFragment`, `ResourceTypeFragment`,
`SecuritySchemeFragment`.

**API structure** — `EndPoint`, `Operation`, `Request`, `Response`, `Body`,
`DocumentationItem`.

**Types** — `BaseShape` plus the seventeen concrete shapes, `Property`,
`PatternProperty`, `Example`, `Examples`, `ScalarFacet`, `DataNode`, `ValueNode`,
`XmlSerialization`.

**Templates and security** — `TraitDefinition`, `ResourceTypeDefinition`,
`SecuritySchemeDefinition`, `SecuritySchemeDescription`,
`SecuritySchemeSettings`, and the two reference forms: `DirectiveRef` for
`type:`/`is:`/`securedBy:`, and `SecurityScheme` for a `securedBy:` entry once
P5 has bound it. An earlier draft of this list named a `Trait` and a
`ResourceType` reference class; neither was built, for the reason
[02](02-architecture.md) § 3 gives, and there is one settings class rather than
six ([09](09-security-and-annotations.md) § A2).

**Annotations** — `DomainExtension`, `DomainLocation`.

**Errors** — `RamlError`, `Trace`, `Position`, `ErrorKind`.

**I/O** — `ResourceLoader`, `FileLoader`, `SafeFileLoader`, `HTTPLoader`,
`SchemeLoader`.

**Not all of these are re-exported from `pyraml` yet.** The top-level `__all__`
currently carries the entry points, the options, the errors, the loaders and the
fragment classes; everything else is reached through its own module. Widening it
is Phase 9's, with the rest of the public-API work.

## 5. Data validation

```python
lib = raml.entry_point
user = lib.types["User"]
err = user.validate({"name": "Bob", "age": 35})  # None on success
```

`validate` returns `None` or a `RamlError`; it does not raise, because the common
use is a boolean-ish check in a request handler. A `validate_or_raise` variant is
provided for the other case.

Matching the reference implementation's example:

```python
raml = parse_from_string(
    "#%RAML 1.0 Library\ntypes:\n  StringType:\n    type: string\n    minLength: 5\n",
    file_name="library.raml",
    base_dir=os.getcwd(),
    options=ParseOptions(validate=True, unwrap=True),
)

t = raml.entry_point.types["StringType"]
t.validate("")  # length must be at least 5
t.validate("abc")  # length must be at least 5
t.validate("more than 5 chars")  # None
t.validate(123)  # invalid type, got int, expected str
```

## 6. Typing

The package ships `py.typed`. Every public function and attribute is annotated;
`mypy --strict` passes on `pyraml/` with the settings in `pyproject.toml`.

Generic facets use `ScalarFacet[T]`, so `string_shape.min_length` narrows to
`ScalarFacet[int] | None` and `.value` to `int`.

Shape narrowing is done with `isinstance` against the concrete classes:

```python
shape = base.shape
if isinstance(shape, ObjectShape):
    for name, prop in shape.properties.items():
        ...
```

A `match` on shape kind is equally supported (the classes are ordinary classes,
usable as `case ObjectShape():` patterns).

## 7. Contracts the consumer must honour

These four contracts appear in the module docstring and in `Raml`'s docstring.
Each has caused problems for users of the reference implementation.

1. **The model may be cyclic.** Recursive types and mutually importing libraries
   both produce cycles. With `unwrap=True`, recursion inside a type is marked
   with `RecursiveShape`, but fragment-level `uses:` cycles remain. Track visited
   ids in any traversal.
2. **The model is mutable and unguarded.** Nothing is frozen, and the parser
   hands out its own objects. Mutating them invalidates the invariants in
   [02](02-architecture.md) § 4.
3. **A `Raml` instance is single-threaded.** Parsing sets re-entrant flags on
   shared objects. Parse in one thread. Read-only traversal afterwards is safe,
   but do not call `validate()` on the same shape from two threads: validating a
   union calls `clone_detached`, which writes `_visiting`.
4. **Without `unwrap=True`, a shape shows only what its own declaration wrote.**
   A `minLength` declared on the parent is not visible on the child until unwrap
   runs.

## 8. CLI

A thin console script, mirroring the reference tool:

```
pyraml validate [-w ROOT] [-r] [-v] FILE [FILE ...]
pyraml info FILE                      # backend, timings, counts
```

`validate` exits non-zero on the first invalid file and prints the rendered trace
chains; `-r` enables remote includes; `-w` sets the workspace root; `-v` repeats
for verbosity. `--json` emits `err.to_dict()` for machine consumption.

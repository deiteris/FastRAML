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
def parse_lenient(
    path: str | os.PathLike[str], options: ParseOptions | None = None
) -> tuple[Raml, RamlError | None]: ...
```

Same work, but returns the partial model alongside the accumulated error instead
of raising. This is what an editor integration uses; it is a thin wrapper, not a
second implementation. It runs the same passes in the same order and stops where
a strict parse stops — **the model is the deliverable, not extra diagnostics.**
The error is the one `parse_from_path` would have raised, complete for the pass
that failed at the granularity [11](11-diagnostics.md) § 2 gives.

### Why it does not continue past the failing pass

It was built that way first, and measured. The passes consume each other's
output, so a pass walking state an earlier one already reported as broken
re-derives the same fault rather than finding a new one:

| Input | strict | continuing past each failure |
|-------|--------|------------------------------|
| one dangling type name, unused | 1 | 2 |
| one dangling type name, 50 dependents | 51 | 102 |
| **a missing library used by 20 types** | **1** | **41** |
| a library with a syntax error, 20 users | 1 | 41 |

P7 re-reports what P1–P3 said, then P9 re-reports P7. Forty-one squiggles for one
unsaved import is worse for an editor than one. The genuinely independent
diagnostics — a security-scheme error *and* an unrelated type error — are
recoverable, but only by skipping the broken **entities** inside P9 and P10
rather than the passes, which is machinery those passes do not have. It is
After-v1 item 6 in [15](15-implementation-plan.md).

### What still raises

Four failures, because none leaves anything to hand back: an unreadable entry
file, a missing or unrecognised RAML header, a root that is not a mapping, and a
fragment whose kind does not match its context.

They are matched on the **head** of the error. Two reasons, and the second is not
obvious. First, the same problem in an *included* file arrives wrapped in the
diagnostic for the include and is a local failure — a library whose root is a
sequence should not abandon a parse of the document that used it. Second, the
tidier-looking test, `raml.entry_point is None`, is wrong in both directions: a
root that is not a mapping fails *after* the fragment is registered so it would
look recoverable, and a bad type declaration fails *before* `entry_point` is
assigned so it would look fatal. The latter is the commonest state an editor
sees, and `decode_fragment` registers the fragment before decoding its body
precisely so that there is something to return.

There is no string-input variant. An editor holding an unsaved buffer supplies a
`file_loader` that shadows it (§ 2) and parses by path, which is also how the
buffer becomes visible to `!include` from other files.

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

**Graph** — `Graph`, `GraphNode`, `Edge`, `Route`, `Entity`, `build_graph`.
`GraphNode.entity` holds the model object the node projects and is never
`None`; `Graph.entity_at` reads it, and `shape_at`/`endpoint_at`/`operation_at`
narrow it ([16](16-graph.md) § 2.7). `Graph.__init__` no longer takes `shapes=`
or `entities=` — the two side maps they filled are gone.

`GraphNode` lives in `pyraml.nodes` with one subclass per node kind — `TypeNode`,
`ResponseNode`, `ParameterNode` and eleven more ([16](16-graph.md) § 2.7). The
kind is the class and the entity's type is a parameter of it, so `node.entity`
is typed for consumers that narrow.

`attributes` is a **property**, derived from the entity on each read
([16](16-graph.md) § 2.8). It cannot be assigned to, and each read returns a
fresh dictionary — a caller reading it in a loop binds it to a local. An
`Operation` carries no `path`: follow `supportedOperation` back to the endpoint,
which has it.

**Types** — `BaseShape` plus the seventeen concrete shapes, `Property`,
`PatternProperty`, `Parameter`, `Example`, `Examples`, `ScalarFacet`,
`DataNode`, `ValueNode`, `XmlSerialization`.

`Parameter` is what `Request.headers`, `Request.query_parameters`,
`Response.headers`, `EndPoint.uri_parameters` and a scheme's `describedBy`
headers and query parameters hold — **not** `Property`, which is what they held
before it existed. It wraps the property and adds the binding, an `id` and the
position of the key; `name`, `base` and `required` read through, so code that
only asked those three needs no change ([05](05-type-model.md) § 5).

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

**What `pyraml` re-exports, and what it does not.** Phase 9 widened `__all__`
from 45 names to 70 — the entry points, options, errors, loaders and fragments as
before, plus everything a consumer **narrows against or walks**: all seventeen
concrete shapes, `BaseShape`, `Property`, `PatternProperty`, `Parameter`, and
`EndPoint`/`Operation`/`Request`/`Response`/`Body`. `isinstance` narrowing is
what § 6 tells a caller to do, and needing `from pyraml.types.complex_ import
ObjectShape` to do it — a module named with a trailing underscore precisely
because it is internal — was a poor advertisement for a supported API.

The rest stay in their own modules **on purpose, not by omission**:
`TypeExprRef`, `DirectiveRef`, `SecurityScheme`, `DomainLocation`, the three
template and security *definition* classes, `Example`/`Examples`,
`XmlSerialization`, and the JSON Schema registry. No consumer exists yet — the
LSP server and the converters are After-v1 item 5 in
[15](15-implementation-plan.md) — and they are what will say which of these a
caller actually reaches for. Exporting them today is a guess, and an exported
name is one you have to keep.

**The surface is not stable before 1.0.** That is stated in the package
docstring and the README rather than left to be inferred from the version, and
it is what makes the paragraph above a working decision rather than a promise.
`tests/unit/test_public_api.py` pins the two properties that would be defects at
any version: every name in `__all__` resolves, and no concrete kind is missing
from it.

The re-exports are loaded on first access and cached. This is invisible to
ordinary imports, wildcard imports, `hasattr` and `dir`; only code inspecting
`pyraml.__dict__` directly can observe that an export is absent before its first
use. The package ships an `__init__.pyi` with eager declarations, so static
analysis and editor completion see the full surface without importing the parser
at runtime.

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

A thin console script, mirroring the reference tool. Presentation only: no
parsing rule lives in `pyraml/cli.py`.

```
pyraml validate [-w ROOT] [--no-workspace-guard] [-r] [-v] [--json] FILE [FILE ...]
pyraml info [-w ROOT] [-r] FILE       # backend, timings, counts
pyraml graph [--format nt|turtle|dot|json] FILE
pyraml list FILE [PATTERN] [--kind K] [--json]             # what is in here
pyraml refs FILE NAME [--kind K] [--depth N] [--limit N]   # what uses this
pyraml deps FILE NAME [--kind K] [--depth N] [--limit N]   # what this is made of
pyraml show FILE NAME [--depth N]     # the effective view of a type or endpoint
pyraml diff OLD NEW [--breaking-only] [--severity S] [--json]
pyraml query FILE (-q SPARQL | -Q FILE.rq) [--json]
```

`validate` and `info` parse with `unwrap=True, validate=True`: their job is to
find faults. The seven graph verbs parse with `validate=False` — a document with
a bad example still has a graph worth reading, and refusing to draw one would
make the tool useless exactly where navigating is most wanted.

- `-w ROOT` sets the workspace root; `--no-workspace-guard` disables the sandbox
  entirely, as go-raml's flag of the same name does.
- `-r` enables remote includes. It builds a client from `httpx` or `requests`,
  whichever is installed — pyRAML depends on neither ([03](03-yaml-and-io.md)
  § 5), so the CLI is where one has to be produced, and where a user who asks
  for `-r` without either gets told so.
- `-v` reports each file and its timing on stdout; `-vv` adds the backend and
  the model counts.
- Diagnostics go to **stderr**, everything else to stdout, so `-v` stays
  pipeable. A valid file with no `-v` prints nothing at all.

**It validates every file and exits 1 at the end**, rather than stopping at the
first failure — matching `raml validate`, and because the case the tool exists
for is running it over a directory in CI. An earlier draft of this section said
"exits non-zero on the first invalid file"; that would have made it useless for
exactly that.

`--json` emits **one JSON object per file**, JSON Lines:

```json
{"path": "api.raml", "valid": false, "error": {"traces": [{"stack": [...]}]}}
```

`error` is `RamlError.to_dict()` or `null`. The wrapper is what `to_dict()` alone
cannot express — which file, and whether it was valid at all — and the `traces`
value inside it keeps the reference implementation's shape, so the two tools can
be diffed fixture by fixture without an adapter ([14](14-testing.md) § 1.3).
Nothing is written to stderr in this mode.

### 8.1 The graph verbs

`graph` writes the whole projection: Turtle by default, or N-Triples, Graphviz
DOT, or plain JSON. The vocabulary and the IRI scheme are
[16](16-graph.md) §§ 2–3.

`list` is the **inventory**, and it comes first: every other navigation verb
takes a NAME, and this is how you learn one.

```
Type          api.raml:6136   userMe
EndPoint      api.raml:2515   /login
Trait         lib.raml:12     hasConflict
```

Exactly the set `find` resolves to one node — every declaration (type, trait,
resource type, security scheme, annotation type) plus endpoints and operations.
**Not every node.** The nodes inside a declaration outnumber the declarations
about twenty to one on a real document and are reached by walking rather than by
naming; listing them would bury the answer in the question. `PATTERN` filters by
substring, case-insensitively, and `--kind` narrows as it does on `refs`.

A name it prints is a name the other verbs accept — there is a test asserting
that for every row, because a listing whose names `show` then rejects would be
worse than none.

Until this verb existed the only ways to learn a name were `graph --format json`
piped through a filter, a catalogue query needing `pyoxigraph`, or guessing.
`info` counts — `types 1288` — which is not the same question.

`refs` and `deps` are one traversal in two directions — `refs` walks the edges
backwards from a named type to everything that can carry it, `deps` walks them
forwards to everything it is built from. Each result line is a **route**, not
just a hit:

```
Operation  api.raml:61   get -returns-> 200 -payload-> ... -inherits-> CallerType
```

Kind and position first — *what* was found and *where to go* — then the route,
which is *why* it was found and the part that varies in length. That route is
the output a property path cannot produce ([16](16-graph.md) § 5), and it is the
reason these are not simply a canned SPARQL query.

**Bound the output.** One type at a real scale produces thousands of routes,
which scrolls the answer off the screen as surely as printing nothing:
`refs errorScheme` on a 149-endpoint API is 1902 lines.

`--limit` therefore defaults to 50. The remainder is reported on stderr, so a
piped run is unaffected, and `--limit 0` still means all. `--kind` keeps only
results of a given kind and is repeatable (`--kind Operation --kind EndPoint`).
`--depth` stops the walk after N hops.

`deps` follows type structure for a type, and containment for anything else. An
endpoint's own edges are `supportedOperation`, `parameter` and `securedBy`, none
of which the type closure follows — so before this rule, `deps` reported that
every endpoint and every operation in a document was made of nothing.

`NAME` is a declared name or a whole node IRI. A **declaration** wins over any
node inside one that happens to carry the same name ([16](16-graph.md) § 3.3).
Two libraries declaring one name is a real ambiguity: the verb lists the
candidates and exits 1 rather than picking one, and the IRI it prints is what you
pass back.

A name that matches nothing gets the near ones, from the same set `list` prints:

```
Usre: no such node
did you mean: User, UserList?
```

Suggested, never substituted — running the nearest name answers a question the
caller did not ask, which is the same reason an ambiguity is reported rather than
resolved. Still exit 1. `difflib` alone is not enough: it is ratio-based, so a
half-remembered fragment (`List` against `UserList`) scores below any usable
cutoff, and a substring pass covers what it misses.

`show` prints the **effective view** of one type as RAML: every inherited
property in one place, each constraint beside the property it constrains, and
each property tagged with the file and line it was really written on
([16](16-graph.md) § 9).

```
Admin:                # api.raml:46
  type: object
  inherits: [User, Entity]
  properties:
    level: integer    # api.raml:49
    name: string      # User, api.raml:44
    id:               # Entity, api.raml:31
      type: string
      maxLength: 36
```

`NAME` may be a **type, an endpoint or an operation**.

An endpoint is the harder case and the one this helps most: it shows the
resource type and traits applied, security after inheritance, ancestor URI
parameters, and every merged-in header, query parameter and body — each tagged
with the trait or resource type that supplied it where that can be established
exactly ([16](16-graph.md) § 9.4).

An operation renders under the resource it hangs off. The operation node carries
no path of its own, so the path is read back over the `supportedOperation` edge;
reading one off the operation produced an empty resource key, and output that
would not load.

A trait or a resource type has no effective form of its own, because it is a
template applied elsewhere. For those, `show` reports where the declaration was
written and how many sites apply it, then points at `refs`.

Each security scheme's `describedBy` appears as its own block under
`securedBy:`, so the `Authorization` header a caller must send is visible.
**One block per scheme, never merged**: `securedBy: [a, b]` means *any* of them,
so hoisting every scheme's headers into `headers:` would claim all of them are
sent at once ([16](16-graph.md) § 9.8).

The output is loadable YAML, so it pastes back into a document and two versions
diff. `--depth` expands nested types; the default of 1 names them instead, which
is what keeps the output the size of a screen.

`query` runs SPARQL, and needs **`pyoxigraph`**, which pyRAML does not depend on
— `pip install pyraml[graph]`, or the verb tells you so and exits 1. All four
result forms work: SELECT as TSV or `--json` JSON Lines, ASK as `true`/`false`,
CONSTRUCT and DESCRIBE as N-Triples.

```
pyraml query --list                  # the catalogue: 17 named questions
pyraml query --show unused-types     # print one, to read or to edit
pyraml query api.raml -n type-fan-in # run one
pyraml query api.raml -q '<sparql>'  # or -Q file.rq
```

`--list` and `--show` need neither a store nor a document, so a reader without
`pyoxigraph` can still find out what the tool would ask. `-n` is checked against
the catalogue **before** the file is opened, so a mistyped name reports the
mistyped name rather than a parse error. The catalogue and the verdict on
whether it earns its keep are [16](16-graph.md) § 6.

### 8.2 `diff`

What changed between two versions, graded by whether it breaks a caller
([16](16-graph.md) § 10). **Exits 1 when anything is breaking**, so it works as
a CI gate without parsing its output.

```
breaking  response-property-removed
    types/Order .discount
    /orders get -> 200 application/json .discount
```

Results are grouped by rule: one edit reaches the declaration and every endpoint
carrying it, and all of those are worth seeing while three copies of the same
sentence are not.

`kind` is `added`, `removed` or `changed` for a node, and `linked` or `unlinked`
for a reference that now points somewhere else — a `securedBy` swapped from
OAuth 2.0 to an API key moves no node and alters no attribute
([16](16-graph.md) § 10.4).

`--json` emits the whole change list — `kind`, `iri`, `node_kind`, `directions`,
the attribute and its values, plus the `rule`, `severity` and `because`. That is
the programmatic surface: a team that disagrees with the built-in policy can
grade the same facts its own way.

`directions` is a **list**, because a type can be a request body and a response
body at once and is graded on the worse side. An earlier version wrote one side,
which put `"direction": "request"` beside `"rule": "response-property-optional"`
in the same record — a consumer regrading these facts could not have reached the
published answer from them.

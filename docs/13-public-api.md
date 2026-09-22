# 13 — Public API

The surface a consumer sees. Everything not listed here is private and may change;
the package exports exactly this and marks the rest with a leading underscore or
keeps it out of `__init__`.

## 1. Entry points

```python
from fastraml import parse_from_path, parse_from_string, ParseOptions

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

Later passes do not run after a pass fails because they require that pass's
output. Accumulators still report independent failures within the pass at the
boundaries listed in [11](11-diagnostics.md) section 2.

### What still raises

An unreadable entry file raises before parsing starts. During parsing, four
outer message keys are fatal only when the head location is the entry URI:
`unknown fragment kind`, `fragment kind not supported`, `unexpected fragment
kind`, and `must be map`.

The location check keeps the same failure recoverable in an included fragment,
including a type-fragment include whose error is not wrapped. When a nonfatal
decode error occurs before `entry_point` is assigned, `parse_lenient` returns the
fragment already registered by `decode_fragment`.

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
| `retain_source` | keep raw node trees (`raml.source_node(uri)`), source text (`raml.source_texts`) and the shape→node index (`raml.source_info`). Off by default; costs memory. |
| `workspace_root` | widen the I/O sandbox and the base for RAML-absolute includes. Default: the entry file's directory. |
| `max_include_size` | per-file cap for `!include` targets. |
| `file_loader` | replace the `file://` loader (e.g. to shadow unsaved buffers). **Disables the built-in sandbox** — see [03](03-yaml-and-io.md) § 5. |
| `http_client` | supply a client to enable remote includes. Absent ⇒ `http(s)` URIs are rejected. |
| `regex_engine` | `"re"` (default, ECMA-ish, backtracking) or `"re2"` (linear time; requires `google-re2`). Use `"re2"` for untrusted input — noting that it covers every regex fastRAML compiles but **not** the ones executed inside an external JSON Schema ([01](01-scope-and-coverage.md) D3). |
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
    source_texts: dict[str, str]  # retain_source only

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

**Graph** — `Graph`, `GraphNode`, `Edge`, `Route`, `Entity`, `build_graph`,
`RAML_NS` (the RDF namespace, provisional until 1.0).
`GraphNode.entity` holds the model object the node projects and is never
`None`; `Graph.entity_at` reads it, and `shape_at`/`endpoint_at`/`operation_at`
narrow it ([16](16-graph.md) § 2.7). `Graph.__init__` no longer takes `shapes=`
or `entities=` — the two side maps they filled are gone.

`GraphNode` lives in `fastraml.nodes` with one subclass per node kind — `TypeNode`,
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

**Values** — `same_value`, the semantic equality `uniqueItems` and enum
membership share: `1` and `1.0` are the same item, `1` and `True` are not. It is
exported because it is a *rule of the language* rather than a utility, so a
consumer that needs it has only two options, and the other one is to write its
own — which is what [17](17-consumers.md) § 2 exists to prevent. `raml-mock`
generating a `uniqueItems: true` array is the case that asked.

**Views** — `backward`, `backward_markdown`, `build_graph`, `build_tree`, `to_json_schema`, `to_openapi`, `address`,
`Addresses`, `Graph`, `Edge`, `Route`, `OAS3Document`, and `Conversion`. The last is there
because `to_json_schema` builds a *fresh* `Conversion` per call, so converting
many shapes that way repeats every definition instead of sharing one table.
`fastmcp-raml` converts every parameter, body and response of an API into one
document, which is the shape of the problem `Conversion` exists for; without it
the exported function is the only supported entry point and it cannot express
the job. See [16](16-graph.md) § 12.

**I/O** — `ResourceLoader`, `FileLoader`, `SafeFileLoader`, `HTTPLoader`,
`SchemeLoader`.

**What `fastraml` re-exports, and what it does not.** Phase 9 widened `__all__`
from 45 names to 70, and the views and the two above have since taken it to 85 —
the entry points, options, errors, loaders and fragments as
before, plus everything a consumer **narrows against or walks**: all seventeen
concrete shapes, `BaseShape`, `Property`, `PatternProperty`, `Parameter`, and
`EndPoint`/`Operation`/`Request`/`Response`/`Body`. `isinstance` narrowing is
what § 6 tells a caller to do, and needing `from fastraml.types.complex_ import
ObjectShape` to do it — a module named with a trailing underscore precisely
because it is internal — was a poor advertisement for a supported API.

The rest stay in their own modules **on purpose, not by omission**:
`TypeExprRef`, `DirectiveRef`, `SecurityScheme`, `DomainLocation`, the three
template and security *definition* classes, `Example`/`Examples`,
`XmlSerialization`, and the JSON Schema registry. The LSP server remains
After-v1 item 5 in [15](15-implementation-plan.md), and it will say which of
these a caller actually reaches for. Exporting them today is a guess, and an
exported name is one you have to keep.

**The surface is not stable before 1.0.** That is stated in the package
docstring and the README rather than left to be inferred from the version, and
it is what makes the paragraph above a working decision rather than a promise.
`tests/unit/test_public_api.py` pins the two properties that would be defects at
any version: every name in `__all__` resolves, and no concrete kind is missing
from it.

The re-exports are loaded on first access and cached. This is invisible to
ordinary imports, wildcard imports, `hasattr` and `dir`; only code inspecting
`fastraml.__dict__` directly can observe that an export is absent before its first
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

Matching go-raml's example:

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
`mypy --strict` passes on `fastraml/` with the settings in `pyproject.toml`.

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
Each has caused problems for users of go-raml.

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

   **So `validate()` and `validate_or_raise()` assert the shape is unwrapped**
   (invariant I12, [02](02-architecture.md) § 4). An un-flattened declaration
   cannot answer "does this value conform?": a child whose parent declared a
   required property has no such property yet, so it accepts a value that omits
   it, and it does so silently.

   Parse with `unwrap=True`, or unwrap a detached clone:

   ```python
   copy = unwrap_shape(raml, shape.clone_detached())
   finish_unwrap(raml, roots=[copy])   # also settles union dispatch tables
   copy.validate(value)
   ```

   `ParseOptions(validate=True)` does this per declared type, which is how it
   works without `unwrap=True`, and why § 4 recommends passing both.

## 8. CLI

A thin console script. Presentation only: no parsing rule lives in
`fastraml/cli.py`.

```
fastraml validate [-w ROOT] [--no-workspace-guard] [-r] [-v] [--json] FILE [FILE ...]
fastraml info [-w ROOT] [-r] FILE       # backend, timings, counts
fastraml graph [--format nt|turtle|dot|json] [-o FILE] FILE
fastraml openapi [--format yaml|json] [-o FILE] FILE       # export OpenAPI 3.0.3
fastraml tree [--positions] [-o FILE] FILE       # the whole document, addressed
fastraml serve [--host H] [--port P] FILE       # the document in a browser (needs fastraml-viewer)
fastraml list FILE [PATTERN] [--kind K] [--json]             # what is in here
fastraml refs FILE NAME [--kind K] [--depth N] [--limit N]   # what uses this
fastraml deps FILE NAME [--kind K] [--depth N] [--limit N]   # what this is made of
fastraml show FILE NAME [--depth N]     # the effective view of a type or endpoint
fastraml compat OLD NEW [--types] [--breaking-only] [--severity S] [--json] [-o FILE]
fastraml query FILE (-q SPARQL | -Q FILE.rq) [--json] [-o FILE]
fastraml lint [--config FILE] [--severity S] [--fail-on S] [--rule ID[=SEVERITY|off]]
              [--format human|text|json|summary]
              [--max-findings N] [--max-findings-per-rule N]
              [--no-color] [--list-rules] [--explain RULE] [--metrics]
              [-o FILE] FILE [FILE ...]
fastraml skills (list | get NAME... | install [NAME...]) [--full] [--json]
                [--user | --dir PATH] [--force]          # the served agent guides
```

`tree` emits the compiled effective view. Its top-level envelope identifies
`format: "fastraml-tree"`, `format_version: 1`, and `view: "effective"`; the
entry point's own `version` remains the API version declared by the RAML author.

`validate` and `info` parse with `unwrap=True, validate=True`: their job is to
find faults. The eleven view verbs parse with `validate=False` — a document
with a bad example still has a graph worth reading, and refusing to draw one
would make the tool useless exactly where navigating is most wanted.

- `serve` is the only view verb that needs a package: `fastraml-viewer`, as the
  `serve` extra, imported inside the verb the way `query` imports
  `pyoxigraph` ([17](17-consumers.md) § 2). It hands the `tree` projection to
  the bundle's own server, which routes it at `api.json` in front of the sample
  the bundle ships, so the page reads this document rather than the one it
  demoed with. It binds `127.0.0.1` by default — a local API description is not
  something to advertise on a network interface by default — and prints the
  chosen URL on stderr, where diagnostics go. `--host` and `--port` name the
  socket; the exit code is still the parse's.

- `-w ROOT` sets the workspace root; `--no-workspace-guard` disables the sandbox
  entirely, as go-raml's flag of the same name does. The root **defaults to the
  entry file's directory**, so an API whose libraries sit beside it rather than
  beneath it is refused on its first `!include` — the commonest first encounter
  anyone has with this tool. The refusal therefore names the flag *and* the
  smallest root that would have worked, which is the nearest directory holding
  both the current root and the path that was refused. `loaders.py` computes it
  into `WorkspaceEscapeError.info['suggested_root']`, because it is the only
  layer holding both paths; `cli.py` writes the sentence, because the flag is
  its vocabulary and a library consumer has no `-w`. No suggestion is made where
  widening would reach a filesystem or drive root: that hands over every file
  the process can reach, which is the sandbox the refusal exists to keep.
- `-o FILE` writes to a file rather than stdout, on every verb whose output is a
  *document* — `graph`, `openapi`, `tree`, `query` — and on `lint`, whose report
  is commonly committed as CI output. The file is opened UTF-8 with
  LF newlines whatever the platform, which is the whole reason the flag exists
  rather than a shell redirect: on Windows a redirect writes CRLF, and committed
  output then differs from what CI regenerates. `viewer/public/api.json` is
  `tree` output this repository commits, so that hazard is not hypothetical.
- `lint` defaults to its grouped `human` report. It colours severities only when
  stdout is an interactive terminal; `--no-color`, `NO_COLOR`, a pipe and `-o`
  produce plain output. `--format text` is the compact line-oriented form for an
  agent reading stdout directly, while `--format json` is for a program that
  parses the versioned report before presenting it to any reader.
- `-r` enables remote includes. It builds a client from `httpx` or `requests`,
  whichever is installed — fastRAML depends on neither ([03](03-yaml-and-io.md)
  § 5.1), so the CLI is where one has to be produced, and where a user who asks
  for `-r` without either is pointed at `fastraml[http]`.
- `-v` reports each file and its timing on stdout; `-vv` adds the backend and
  the model counts.
- Diagnostics go to **stderr**, everything else to stdout, so `-v` stays
  pipeable. A valid file with no `-v` prints nothing at all.
- **`--severity S` is a threshold on every verb that has it** — *S and
  everything worse* — and never a membership test, because one flag name cannot
  mean two things in one tool. The scales
  themselves stay separate, because they measure different things: `compat`
  grades `breaking|review|compatible|cosmetic`, what a change does to a caller, and `lint`
  grades `error|warning|info`, how much a finding should block CI
  ([18](18-linting.md) § 1). `--breaking-only` is now `--severity breaking`
  said shorter, and is kept because it is what a CI gate reaches for.
- `lint --rule ID[=SEVERITY|off]` is a repeatable one-run override applied after
  its configuration file. A bare ID enables the rule, a severity enables and
  regrades it, and `off` disables it. Duplicate or unknown IDs are errors.
- Every parsing verb accepts `--config FILE`. The common YAML root has `parser:`,
  `lint:` and `compatibility:` sections. CLI flags override parser settings;
  verb-required options such as unwrap and validation are not configurable.

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
value inside it keeps go-raml's shape, so the two tools can
be diffed fixture by fixture without an adapter ([14](14-testing.md) § 1.3).
Nothing is written to stderr in this mode.

### 8.1 The graph verbs

`graph` writes the whole projection: Turtle by default, or N-Triples, Graphviz
DOT, or plain JSON. The vocabulary and the IRI scheme are
[16](16-graph.md) §§ 2–3.

`tree` writes the whole document as an addressed JSON tree, and is the
counterpart to `graph`: one `Walk` assigns both, so an address it prints names
the node `graph` prints ([16](16-graph.md) § 11). Use it when a consumer needs
the *contents* — examples, defaults, every container inline — which the graph
deliberately does not carry. `--positions` writes the span of every declaration
instead.

`openapi` writes the effective API as OpenAPI 3.0.3, YAML by default or JSON
with `--format json`. Information with no exact
OpenAPI representation is kept
under an `x-raml-*` extension where a useful representation exists and reported
as a warning on stderr; stdout therefore remains a parseable document. The
Python surface is `to_openapi(raml) -> (OAS3Document, dropped)` ([16](16-graph.md)
§ 13).

```python
from fastraml import ParseOptions, address, build_tree, parse_from_path

raml = parse_from_path('api.raml', ParseOptions(unwrap=True))
document = build_tree(raml)         # the tree, references as addresses
where = address(raml)               # entity id -> address, on its own
```

`Addresses.of` is many-to-one and `id` remains the identity: a linked
declaration and its link target share one address on purpose
([16](16-graph.md) § 3.1). Pass `Graph.addresses` to `build_tree` when you hold
both views: the walk is most of the cost, and the two have to agree on it.

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

`query` runs SPARQL, and needs **`pyoxigraph`**, which fastRAML does not depend on
— `pip install fastraml[graph]`, or the verb tells you so and exits 1. All four
result forms work: SELECT as TSV or `--json` JSON Lines, ASK as `true`/`false`,
CONSTRUCT and DESCRIBE as N-Triples.

```
fastraml query --list                  # the catalogue: 9 named reports
fastraml query --show endpoint-tree    # print one, to read or to edit
fastraml query api.raml -n type-fan-in # run one
fastraml query api.raml -q '<sparql>'  # or -Q file.rq
```

`--list` and `--show` need neither a store nor a document, so a reader without
`pyoxigraph` can still find out what the tool would ask. `-n` is checked against
the catalogue **before** the file is opened, so a mistyped name reports the
mistyped name rather than a parse error. The catalogue and the verdict on
whether it earns its keep are [16](16-graph.md) § 6.

### 8.2 `compat`

What changed between two versions, graded by whether it breaks a caller
([16](16-graph.md) § 10). **Exits 1 when anything is breaking**, so it works as
a CI gate without parsing its output.

That exit code is also why `-o` matters more here than on the export verbs: a
shell redirect leaves a failed command and no way to tell a report that was
written from one that was not, on top of the CRLF problem `-o` exists for
(§ 8 above). An unwritable path is reported on stderr *instead of* the verdict,
because a report nobody could write is a failure of the command rather than a
finding about the API. The exit code and the breaking count are unchanged, and
`--json` goes through the same flag.

```markdown
## `GET /orders`

### Request

| Where | Path | Change | Before | After | Compatibility |
|---|---|---|---|---|---|
| Security |  | Authentication | Optional | Required | Breaking |
| query parameter `limit` |  | Requiredness | Optional | Required | Breaking |
| query parameter `limit` | `$` | `type` | string | integer | Breaking |

### Response

| Where | Path | Change | Before | After | Compatibility |
|---|---|---|---|---|---|
| Response `200` body `application/json` | `$.discount` | Property removed | optional number |  | Breaking |
```

Results are grouped by operation, then by which side of the wire the change sits
on: **Request**, **Response** and **Documentation**, the last for prose that
changed on neither. A caller fixes what it sends before it can see what it
receives, and a parameter's contract and its shape are one question, so they sit
adjacent rather than in tables split by which class produced them. Transport and
security count as the request side: a protocol the caller cannot speak and a
credential it must now present both stop the call before a response exists.

Each side is then split into **Removed**, **Changed** and **Added**, worst kind
first. `Before` and `After` fit only the middle one, so a `Changed` table states
its transition in a single `Detail` cell as `old -> new`, and the other two need
no `Change` column because their heading is the verb. A `Description` column
appears where the author described the entity that arrived or left -- the one
thing an addition can say that its position cannot.

A row states nothing its heading or its `Where` column already said: under
**Response** a cell opens at its status, and `Path` appears only where the change
reaches inside a shape, so a table of contract changes has no `Path` column at
all. JSON is unaffected -- `path` is still `[]` at a shape root. A rendered shape
path starts at `$`; properties append `.name`,
array items append `[]`, and union members append their RAML name or type in
angle brackets, such as `<Error>` or `<integer>`. Identical members use `#2`,
`#3` and so on only where an occurrence is needed to disambiguate them. Dot
notation is used only for identifier-like property names; every other name uses
JSON bracket notation, for example `$["user.name"]`, so punctuation in a RAML
name cannot be mistaken for path structure. Pattern properties retain the
slashes that distinguish them, for example `$[/^x-/]`.

A shared authored type used by five operations produces five effective schema
changes, because those are the contracts an external caller uses. Authored
declarations and graph IRIs do not appear. Markdown rolls identical rows up into
a **Several operations** section naming the operations each reaches, and counts
those rolled-up entries in its impact summary, so the headline states how many
decisions there are rather than how far one reached. The records, the overrides
and the exit code are unaffected.

A declaration the API root makes and an operation may override is compared once
at the root when both versions inherit it, and per operation only where one
states its own. `baseUri` (which has no override), `baseUriParameters`,
`protocols:` and `securedBy:` all work this way, and Markdown gathers them under
**Every operation**. Reported per operation instead, one `protocols:` edit in
`examples/compatibility/` produced 33 identical rows and 33 of 54 breaking
changes.

An added or removed resource with no operation produces no change. An operation
appearing or disappearing is an `OperationAdded` or `OperationRemoved`; its
nested request and responses are subsumed. Markdown renders those availability
changes as separate added/removed lists under **Operations added and removed**,
not as one-row operation tables. Each list item includes the present operation's
`displayName` and `description` when supplied; the same fields are present in
its JSON record. Every one is a `Changed`; `operation` and `path` say which of
the four scopes it sits in, and a global parameter shape has a path and no owner.

`--json` emits one typed record per line. API changes have `scope: api`, and API
parameter shapes have `scope: api-schema`;
matched-operation changes have `scope: operation` or `scope: schema` plus a
typed `location`, `subject`, `attribute`, `before`, `after`, `impact` and `rule`.
Only a schema record has `path`: `[]` for its root or a segment array for a
nested shape. Operation records do not carry a placeholder path. There is no
`iri`, `node_kind` or reconstructed `directions` field.

`subject` names what changed and comes from a closed vocabulary; `attribute`
names the field within it and may be `null`. Reading them together is what tells
a consumer whether a `true` is a requiredness or a facet value
([docs/16](16-graph.md#101-the-change-list-is-the-contract) § 10.2). `kind` says
which side of an `added` or `removed` record is populated; the other is `null`
rather than a sentinel. The populated side carries only what `location` and
`path` do not already state — a property's or parameter's type and requiredness,
a security alternative's scheme name. A response, a body and a union member
carry `null` on both sides, because their status, media type and member type are
in the coordinate. An added or removed operation carries neither and is addressed
by `operation` and `rule`.

Records come out in walk order, which is declaration order. Markdown sorts rows
within each table by impact and leaves the records alone, because `impact` is
what a project override rewrites.

Markdown is a bounded reading view, not the lossless record. Pipes and line
breaks are escaped inside tables, arbitrary names use safe variable-length code
spans, and added/removed operation metadata is rendered as plain text rather
than executable Markdown. Descriptions show the first non-empty line, capped at
160 characters with `...` when content was omitted. JSON retains the complete
original display names, descriptions and before/after values.

### 8.3 `skills`

The one verb that reads no RAML. It prints the agent guides shipped in
`fastraml/skilldata/`, which is a directory of Markdown inside the package rather
than a file in the repository.

```
fastraml skills list                 # the guides, and what each covers
fastraml skills get core             # print one
fastraml skills get core --full      # and its references/ files
fastraml skills get backward sparql  # several, separated by `---`
```

**An agent skill installed elsewhere is a copy, and a copy goes stale against
the binary that answers.** Serving the text from the package inverts that: the
installed skill is a *stub* whose whole body points at `fastraml skills get core`,
so the instructions an agent reads are the ones that shipped with the version it
is about to run. The stub in `skills/fastraml/` is therefore deliberately thin,
and the content is deliberately not duplicated there. The pattern is
[agent-browser](https://github.com/vercel-labs/agent-browser)'s.

A guide is a directory with a `SKILL.md` and an optional `references/`, matching
the [Agent Skills specification](https://github.com/agentskills/agentskills) so
that a guide can also be installed directly. `name` and `description` come from
its frontmatter; a directory whose frontmatter will not parse still prints,
under its directory name, because a guide that cannot be read is worse than one
that is mislabelled.

**Four of the five guides are about this CLI; `raml` is about the language.**
`core`, `lint`, `backward` and `sparql` document verbs, so they go stale with the
build that serves them — which is the whole reason for serving rather than
shipping a copy. `raml` condenses the RAML 1.0 specification for an agent writing
or reviewing a document, and is served from the same place for a narrower reason:
it records where fastRAML departs from the specification, and *that* goes stale
with the build. Its two `references/` files hold the facet and node tables, so
the body stays within the specification's size guidance for a loaded `SKILL.md`.

`lint` carries the half of the verb that is not the command line: which rules
ship and why only `spec` runs by default, `match:` as the alternative to
disabling a rule, and the two protocols a plugin implements
([18](18-linting.md) §§ 3, 6). A house rule is the one thing this project
deliberately does not ship, so the guide has to say how to write one.

**A guide naming a catalogue entry is a cross-reference that rots.** The
`query` catalogue is not frozen — eight of its entries became lint rules — and
two guides went on citing `unused-types` after it left. `tests/unit/test_cli.py`
now extracts every query and rule name a guide cites and asserts it still
resolves, alongside the check that every `skills get X` a guide suggests names a
guide this build serves.

A missing name is **named, not guessed** — the verb lists what exists and exits
1, for the same reason `_resolve` refuses to pick between ambiguous nodes.

The verb takes none of the common flags: there is no document, so a workspace
root, a guard and a remote client would all be noise.

#### Installing

```
fastraml skills install                    # the stub, into ./.agents/skills/
fastraml skills install --user             # into ~/.agents/skills/
fastraml skills install --dir ~/.claude/skills   # anywhere else
fastraml skills install core backward --force  # a guide, replacing what is there
```

**`.agents/skills` and not a client's own directory.** The Agent Skills
specification names it the cross-client path, and Claude Code, GitHub Copilot
and VS Code all scan it at both project and user scope — so one copy serves
every agent rather than one copy per agent. `--dir` reaches `~/.claude/skills`,
`~/.copilot/skills` or anything else for a client that wants its own.

Project scope is the default, matching the ecosystem's installers: the skill
then travels with the repository it was installed for, and can be committed
beside it.

**This is built in rather than delegated to `gh skill`.** That tool does the
same job across thirty agents and would have been the obvious answer, but it is
third-party, in preview, and not guaranteed to be present — and the operation is
a file copy into a documented directory. Needing a second CLI for that would
have been the only hard dependency this package has. Nothing stops a user
running `gh skill install deiteris/FastRAML`: the repository layout already
satisfies its `skills/*/SKILL.md` discovery rule, which `gh skill publish
--dry-run` confirms.

Installing **refuses to overwrite without `--force`**, and checks every
collision before the first write. An installed skill is a file the user may have
edited; replacing it silently is the one thing an installer must not do, and a
half-finished install across several names leaves the user to work out which
ones landed.

With no name it installs the **stub**, not a guide. Installing `core` by default
would defeat the arrangement, because that copy is exactly what goes stale. The
stub carries `hidden: true`, so `list` does not advertise it while `get` and
`install` still accept it by name — a listing of documentation should not
recommend the shim whose only job is to fetch that documentation.

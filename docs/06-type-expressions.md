# 06 — Type expressions (RDT)

RDT — "RAML Data Type expression" — is the tiny language in a `type:` scalar:
`Person`, `string[]`, `(Manager | Admin)[]`, `lib.Thing?`.

go-raml generates a lexer/parser for it with ANTLR
(`go-raml-main/rdt/rdtLexer.g4`, `rdtParser.g4`). pyRAML does not
(deviation **D8**): the ANTLR Python runtime is a heavy dependency with poor
constant factors, and this grammar is nine productions with no ambiguity.

## 1. Grammar

Reproduced from the reference implementation, unchanged:

```
entrypoint : expression EOF ;
expression : union ;
union      : type ( '|' type )* ;
type       : ( primitive | group | reference ) '[]'* '?'? ;
group      : '(' expression ')' ;
primitive  : 'string' | 'integer' | 'number' | 'boolean' | 'datetime'
           | 'time-only' | 'datetime-only' | 'date-only' | 'file'
           | 'nil' | 'any' | 'array' | 'object' | 'union' ;
reference  : IDENTIFIER ( '.' IDENTIFIER )? ;
IDENTIFIER : [0-9a-zA-Z_.-]+ ;
WS         : [ \t]+ -> hidden ;
```

Observations that drive the implementation:

- `IDENTIFIER` **includes `.`**, and `reference` also allows an explicit dotted
  form. Both spellings reach the same place: the resolver splits on the *last*
  dot ([04](04-fragments-and-namespaces.md) § 3). So `a.b.c` is library `a.b`,
  type `c` — and since `a.b` will not be a key in `uses:`, namespace chaining
  fails, as the spec requires.
- Primitive keywords are matched before `IDENTIFIER`, so a user type literally
  named `string` is unreachable — consistent with the parser rejecting
  redefinition of built-in names anyway.
- `[]` binds tighter than `|`: `Person | Animal[]` is `Person | (Animal[])`.
  Grouping is required to say otherwise.
- `?` is postfix on a `type`, after any `[]`s: `string[]?` is an optional array.

Test corpus (`rdt/examples.txt` in the reference) is adopted verbatim as the
first parser fixture set.

## 2. Implementation

### 2.1 Tokenizer

A single pass producing `(kind, text, column)` triples. Whitespace is skipped,
not emitted. ~60 lines, no regex per token — but see the note in
[12](12-performance.md) § 5: a single compiled `re.Scanner`-style master pattern
beats a Python-level character loop, so the tokenizer uses one compiled
alternation and `finditer`.

Columns are 0-based within the expression string. The consumer converts to a file
column by adding `base.type_expr.value_pos.column`.

### 2.2 Parser

Recursive descent, one function per production, returning an immutable AST:

```python
@dataclass(slots=True, frozen=True)
class Primitive:
    name: str
    col: int


@dataclass(slots=True, frozen=True)
class Reference:
    name: str
    col: int  # possibly dotted


@dataclass(slots=True, frozen=True)
class Array:
    item: RdtNode


@dataclass(slots=True, frozen=True)
class Optional_:
    inner: RdtNode  # sugar for `inner | nil`


@dataclass(slots=True, frozen=True)
class Union:
    members: tuple[RdtNode, ...]
```

Only `Primitive` and `Reference` carry a column. They are the only nodes a
consumer positions: `TypeExprRef` is emitted for type names and built-in
keywords, never for the `[]`, `?` or `|` operators, whose extent is implied by
their operands. An `Array`, `Optional_` or `Union` is structural.

Errors carry the column of the offending token, **relative to the expression
string**, and the caller rebases it. The parser is memoised on expression text
alone (§ 2.3) and so cannot know which file it is parsing; a diagnostic raised
here therefore has no location. The caller — which does know — catches it, adds
`base.type_expr.value_pos.column` to the column, and re-raises with the file URI.
That is what lets one malformed expression repeated 500 times cost one parse and
still produce 500 correctly positioned diagnostics.

### 2.3 The expression cache

```python
Raml.expr_cache: dict[str, RdtNode | RdtError]
```

Parsing is **memoised on the expression text**. In a real corpus the same handful
of expressions (`string`, `integer`, `object`, `MyType`, `MyType[]`) appear
thousands of times; go-raml re-runs ANTLR for each. The AST is immutable and
carries no file positions (only intra-expression columns), so sharing is safe.

Parse failures are cached too — a malformed expression repeated 500 times should
cost one parse and 500 dictionary hits, and each of the 500 diagnostics still
gets its own file position because that is supplied by the caller.

## 3. AST → shapes

The builder mirrors go-raml's `RdtVisitor`. Its contract:

> Given an `UnknownShape` *target* whose `base` is already registered and may
> already be referenced by other shapes, replace `target.base.shape` with the
> concrete shape the expression denotes, allocating fresh anonymous `BaseShape`s
> for any inner types the expression implies.

Why fresh anonymous shapes for inner types: in `string[]`, the array and its item
type are two declarations. If both used the same `BaseShape`, facets written
alongside the expression (`type: string[]`, `minItems: 1`) would leak onto the
item type. go-raml's comment says exactly this: "Target is required to isolate
anonymous shapes created by Union, Optional and Array syntax."

Per node kind:

| AST | Action |
|-----|--------|
| `Primitive` | build that kind on `target.base`, passing the pending facets |
| `Reference` | resolve the name via `target.base.anchor` → `ref`; **self-reference is an error**; recursively resolve `ref` if still unknown; build a shape of `ref.type` on `target.base`; then link (§3.1) |
| `Array` | allocate an anonymous `BaseShape` for the item, build the inner expression into it, build an `ArrayShape` on `target.base` with `items = item_base` |
| `Optional_` | allocate an anonymous member base + an anonymous `nil` base; build a `UnionShape` on `target.base` with `anyOf = [member, nil]` |
| `Union` (>1 member) | one anonymous `BaseShape` per member, each built from its sub-AST; `UnionShape` on `target.base` |
| `Union` (1 member) | delegate straight through — not actually a union |

Every anonymous base inherits `target.base.anchor` and `target.base.type_expr`, so
inner references resolve in the right namespace and report the right column.

### 3.1 Alias versus inheritance

A `Reference` produces one of two relationships. One field decides which:

```python
if target.facets is None:  # the declaration was a bare scalar: `type: Foo`
    shape.base.alias = ref
else:  # there were sibling facets: `type: Foo` + `minLength: 5`
    shape.base.inherits.append(ref)
```

`target.facets is None` — as distinct from an empty list — means the declaration
was a scalar node with no accompanying mapping, that is, a pure reference. The
new shape is then an **alias**: it borrows the referent's facets wholesale and is
not treated as a subtype.

When sibling facets are present, the relationship is **inheritance**: the new
shape narrows the referent, and the rules in
[07](07-resolution-and-inheritance.md) apply.

`make_shape` must therefore pass `None`, not `[]`, for the scalar case. A
refactor that normalises the two is a silent behaviour change, so a test asserts
the distinction.

### 3.2 Reference positions for tooling

Each resolved name appends a `TypeExprRef` to the shape:

```python
class TypeExprRef:
    line: int
    column: int
    resolved: BaseShape | None  # a type name
    library_link: LibraryLink | None  # the "lib" part of "lib.Type"
    library_alias: str | None
    builtin: str | None  # a primitive keyword
```

For `lib.Type` two refs are emitted — one for the prefix (navigates to the
library file) and one for the name (navigates to the declaration). Primitives get
a ref with `builtin` set so hover can show documentation. Nothing in the parser
reads these; they cost one small object per name and make a future LSP possible
without re-lexing.

## 4. Where expressions are *not* allowed

Spec § Includes: "the `!include` tag cannot be used in any type expression or
multiple inheritance." Enforced at two points:

- a sequence item under `type:` with tag `!include` → `!include is not allowed in
  multiple inheritance`;
- an expression is a scalar, so an `!include` there is handled as a fragment link
  before the expression parser ever sees it.

Spec § Inline Type Declarations: inline declarations are allowed everywhere a
type is referenced **except** inside a type expression. Structurally guaranteed —
an expression is a string.

Spec § Using XML and JSON Schemas: a type that wraps an external schema "MUST NOT
participate in type inheritance or specialization, or effectively in any type
expression". Enforced by `JsonShape.inherit` rejecting anything but an identical
schema, and by `JsonShape.decode_facets` rejecting any sibling facet.

# 06 - Type expressions (RDT)

This document owns expression syntax, parsing, caching, and construction of
expression-implied shapes. Reference resolution and inheritance semantics are in
[07](07-resolution-and-inheritance.md).

## 1. Grammar

An RDT expression appears in a scalar `type:` value, for example `Person`,
`string[]`, `(Manager | Admin)[]`, and `lib.Thing?`.

```
entrypoint : expression EOF ;
expression : union ;
union      : type ( '|' type )* ;
type       : ( primitive | group | reference ) '[]'* '?'? ;
group      : '(' expression ')' ;
primitive  : 'string' | 'integer' | 'number' | 'boolean' | 'datetime'
           | 'time-only' | 'datetime-only' | 'date-only' | 'file'
           | 'nil' | 'any' | 'array' | 'object' | 'union' ;
reference  : IDENTIFIER ;
IDENTIFIER : [0-9a-zA-Z_.-]+ ;
WS         : [ \t]+ -> hidden ;
```

Identifiers retain dots. The resolver splits a qualified reference at its last
dot; expression parsing does not validate namespace structure. Primitive names
are classified after longest-match identifier lexing.

`[]` binds tighter than `|`; grouping changes that order. `?` follows all `[]`
postfixes, so `string[]?` is `(string[]) | nil`; `string?[]` is invalid.

## 2. Parser and cache

The tokenizer uses one compiled alternation and produces token kind, text, and a
zero-based column relative to the expression. The recursive-descent parser
returns immutable `Primitive`, `Reference`, `Array`, `Optional_`, and `Union`
AST nodes. Only primitive and reference nodes carry columns because only names
are referenceable; operators are structural.

`Raml.expr_cache` is per parse and is keyed by exact expression text. It caches
both ASTs and parse failures. Parser diagnostics therefore have only a
source-relative column; P7 attaches the occurrence's file location and rebased
position.

## 3. Building shapes

P7 replaces an `UnknownShape` on its existing `BaseShape` with the expression's
concrete kind. It creates fresh anonymous bases for expression-implied array
items, optional members, and union members. Those bases inherit the outer
expression's anchor and source expression, but sibling facets stay on the outer
shape.

| AST | Result |
|---|---|
| primitive | the corresponding concrete kind |
| reference | resolve the name, take its kind, and make an alias or inheritance edge |
| array | an array with a fresh item shape |
| optional | a union of a fresh member and `nil` |
| union | a union with one fresh member per operand |

A single syntactic union member is represented directly, not as `UnionShape`.

A bare scalar reference is syntactically a reference expression but semantically
an alias: `Alias: Person` names the same type under another declaration identity.
A mapping declaration inherits even when it contains only `type:`:
`Child: {type: Person}`. This distinction is recorded before P7 because the
original YAML form is no longer available then.

P7 records `TypeExprRef` entries for primitive keywords and resolved names.
Qualified names produce one entry for the library prefix and one for the
referenced declaration.

## 4. JSON Schema operands

JSON Schema types are accepted in arrays, optionals, unions, properties, and
parameter declarations. Such composition delegates instance checking to the
schema validator. A bare reference remains an alias. A JSON Schema type cannot
be narrowed by RAML inheritance; see [10](10-validation.md#7-json-schema).

Implementation: `types/expressions/lexer.py`, `types/expressions/parser.py`, and
`types/resolve.py`. Tests: `tests/unit/test_expressions.py`, `test_resolve.py`,
and `test_jsonschema.py`.

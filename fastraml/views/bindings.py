"""The tree contract, as TypeScript declarations — docs/16-graph.md § 11.11.

A consumer of `fastraml tree` has to know what the JSON holds. Written by hand
that list goes stale the first time a facet is added to a kind, and it goes
stale *quietly*: a key the declarations omit still arrives, and a consumer that
does not read it looks exactly like a document that did not say it. That is the
argument `facet_slots` makes for one facet vocabulary (docs/14 § 4, law 14), and
it applies with more force across a language boundary, where no type checker
spans both sides.

So the declarations are derived, from two sources and by reading source rather
than by importing it:

* **Which keys the projection emits** — from `tree.py`'s own AST. Every
  `_Projector` method builds a dict, and the keys are literals: in the opening
  display, in `out[...] = ...`, or in the `for field in (...)` loops. Required
  and optional fall out of the same read, since a key assigned under an `if` is
  one the projection may omit.
* **What a kind's facets are, and their types** — from the `self.x: T = None`
  annotations in each kind's `__init__`. Python discards those at runtime, so
  this reads them from the AST; the alternative is a table per kind, which is
  the thing being avoided.

What is *not* derived is the value type of a structural key: `out['operations']`
holds an expression, and no reading of it yields `Record<string, Operation>`.
Those are declared in `_STRUCTURAL` below — but only their types. The key sets
are checked against the AST, so **a key added to the projection and not declared
here fails generation by name**. That is the whole point of the arrangement: the
hand-written part cannot silently fall behind, because it is not the part that
says which keys exist.

`python -m fastraml.views.bindings` writes the file. `tests/unit/test_bindings.py`
asserts the checked-in copy is what generation produces.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from typing import TYPE_CHECKING, Final, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ['DESTINATION', 'typescript']

#: Where the generated declarations live. Beside the consumer that reads them:
#: they are an artifact of this repository, not of an install.
DESTINATION: Final = 'viewer/src/tree.d.ts'

_ROOT: Final = pathlib.Path(__file__).resolve().parent.parent.parent


def _strings(elements: object) -> list[str]:
    """The string constants in a literal display, and nothing else.

    `ast.Constant.value` is a union of every literal type, so each use would
    otherwise narrow it again. A non-string in one of these tuples is a
    different mistake and is silently dropped here on purpose: this reads
    source it does not own.
    """
    if not isinstance(elements, list):
        return []
    return [item.value for item in elements if isinstance(item, ast.Constant) and isinstance(item.value, str)]


class _Facet(NamedTuple):
    """One constraint a kind declares, as it arrives in the JSON."""

    name: str
    typescript: str
    #: The Python annotation it was read from, which is what decides the
    #: spelling and is worth keeping so a surprising one can be explained.
    annotation: str
    kind: str


class _Emitted(NamedTuple):
    """What one `_Projector` method puts in its result."""

    required: tuple[str, ...]
    optional: tuple[str, ...]
    #: Methods whose keys are merged in through `out.update(...)`.
    delegates: tuple[str, ...]
    #: True when a key comes from an expression rather than a literal, which
    #: means the result is a map rather than a fixed set of fields.
    dynamic: bool


# -- reading the projection ----------------------------------------------------


def _projector() -> dict[str, _Emitted]:
    """Every key each `_Projector` method emits, read off its own source."""
    source = (_ROOT / 'fastraml' / 'views' / 'tree.py').read_text(encoding='utf-8')
    projector = next(
        node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ClassDef) and node.name == '_Projector'
    )
    return {
        method.name: _emitted(method)
        for method in projector.body
        if isinstance(method, ast.FunctionDef) and not method.name.startswith('__')
    }


def _emitted(method: ast.FunctionDef) -> _Emitted:
    """The keys `method` puts in *its own* result.

    Read from the four statements that can produce one, never by walking every
    node: a nested display is the shape of a *value* -- `head`'s `{'$ref': ...}`
    or a documentation item -- and collecting its keys would attribute them to
    the enclosing record. That is not a hypothetical; it put `content` and
    `$ref` on `EntryPoint` and `Recursion` on the first run.
    """
    required: list[str] = []
    optional: list[str] = []
    delegates: list[str] = []
    dynamic = False

    #: `for field in ('display_name', 'description')` names several keys at
    #: once, and the tuple is a literal, so the loop variable resolves.
    #:
    #: Accumulated, never assigned: `shape()` has two such loops and both call
    #: the variable `field`, so a last-wins mapping silently dropped the first
    #: tuple -- `display_name`, `description` and `required` left the contract
    #: without leaving the projection.
    loops: dict[str, list[str]] = {}
    for node in ast.walk(method):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and isinstance(node.iter, ast.Tuple):
            loops.setdefault(node.target.id, []).extend(_strings(node.iter.elts))

    for node, conditional in _writes(method):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    (optional if conditional else required).append(key.value)
                else:
                    dynamic = True
        elif isinstance(node, ast.Subscript):
            index = node.slice
            if isinstance(index, ast.Constant) and isinstance(index.value, str):
                (optional if conditional else required).append(index.value)
            elif isinstance(index, ast.Name) and index.id in loops:
                optional.extend(loops[index.id])
            else:
                dynamic = True
        elif isinstance(node, ast.Call):
            delegates.extend(_delegates(node))

    at_hand = set(required)
    return _Emitted(
        required=tuple(dict.fromkeys(required)),
        optional=tuple(name for name in dict.fromkeys(optional) if name not in at_hand),
        delegates=tuple(dict.fromkeys(delegates)),
        dynamic=dynamic,
    )


def _writes(method: ast.FunctionDef) -> Iterator[tuple[ast.AST, bool]]:
    """Each node that contributes a key, with whether it runs conditionally.

    A key written under an `if` is one the projection may omit, which is exactly
    TypeScript's `?`. The distinction is free here and is *not* recoverable from
    a sample of output, where a key that was omitted and one that is never
    emitted look identical.
    """
    for node in ast.walk(method):
        conditional = _under_a_branch(method, node)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Dict):
            yield node.value, conditional
        elif isinstance(node, ast.Return) and node.value is not None:
            display = _record(node.value)
            if display is not None:
                yield display, conditional
        elif (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)) or (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'update'
        ):
            yield node, conditional


def _record(value: ast.expr) -> ast.Dict | None:
    """The display a `return` produces, seeing through a list comprehension.

    `schemes` returns `[{...} for scheme in ...]`: a list of records, whose
    element is the record this method describes.
    """
    if isinstance(value, ast.Dict):
        return value
    if isinstance(value, (ast.ListComp, ast.SetComp)) and isinstance(value.elt, ast.Dict):
        return value.elt
    if isinstance(value, ast.List) and len(value.elts) == 1 and isinstance(value.elts[0], ast.Dict):
        return value.elts[0]
    return None


def _under_a_branch(method: ast.FunctionDef, target: ast.AST) -> bool:
    for node in ast.walk(method):
        if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.While, ast.Try)):
            for branch in ast.iter_child_nodes(node):
                if (
                    branch is not getattr(node, 'test', None)
                    and branch is not getattr(node, 'iter', None)
                    and (target is branch or any(target is inner for inner in ast.walk(branch)))
                ):
                    return True
    return False


def _delegates(node: ast.Call) -> list[str]:
    """The `_Projector` methods whose result an `update` call merges in."""
    return [
        inner.func.attr
        for argument in node.args
        for inner in ast.walk(argument)
        if isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Attribute)
        and isinstance(inner.func.value, ast.Name)
        and inner.func.value.id == 'self'
    ]


# -- reading the kinds ---------------------------------------------------------

#: The kinds whose facets the projection inlines onto a shape, in the order the
#: declarations should read. `BaseShape` is not here: its fields are the shape's
#: own, and `shape()` writes them itself.
_KINDS: Final = (
    'ObjectShape',
    'ArrayShape',
    'UnionShape',
    'StringShape',
    'NumberShape',
    'IntegerShape',
    'FileShape',
    'DateTimeShape',
    'JsonShape',
    'UnknownShape',
    'RecursiveShape',
)

#: Slots the projection never emits (`tree.py`'s `_SKIP`), plus the two working
#: buffers. Kept as a list of names rather than read from `_SKIP` because a
#: reader of the generated file should be able to see why each is absent.
_NOT_EMITTED: Final = frozenset(
    {
        'anchor',
        'id',
        'key_pos',
        'raml',
        'value_pos',
        '_raml',
        '_unwrapped',
        '_visiting',
        'type_expr_refs',
        # A working buffer within one decode: facets a union or an unknown kind
        # could not digest yet. Emitted as raw nodes, so it is not a contract.
        'pending_facets',
        # Parser state, removed from the projection deliberately (§ 11.8).
        'link',
        'alias',
        'is_annotation_type',
        'shape',
        'from_mapping',
        'validator',
        'location',
    }
)

#: How `_Projector.value` turns each declared annotation into JSON. One entry
#: per distinct annotation across every kind -- twenty-eight of them, so this is
#: a translation of a closed set rather than a table that can grow behind us.
#: `_kinds` raises on an annotation missing from here, naming it.
_JSON_OF: Final = {
    'ScalarFacet[int] | None': 'number',
    'ScalarFacet[str] | None': 'string',
    'ScalarFacet[bool] | None': 'boolean',
    # A `Fraction` is stringified rather than divided: a facet's value is built
    # from the raw scalar text, and `float` would lose it (CLAUDE.md).
    'ScalarFacet[Fraction] | None': 'string',
    # The pattern's source text, not a compiled object.
    'ScalarFacet[re.Pattern[str]] | None': 'string',
    'list[ScalarFacet[str]] | None': 'string[]',
    'BaseShape | None': 'Shape | Ref',
    'list[BaseShape] | None': '(Shape | Ref)[]',
    'dict[str, Property] | None': 'Record<string, Property>',
    'dict[str, PatternProperty] | None': 'Record<string, PatternProperty>',
    # `discriminator_value` is user data, so it is whatever the author wrote.
    'DataNode | None': 'Json',
}


#: Slots that point *back* at something the walk is already inside. `tree.py`
#: emits a bare `{'$ref': ...}` for these rather than descending, so their
#: declared `BaseShape` is not what arrives. Read from there so the two cannot
#: disagree.
def _back_pointers() -> frozenset[str]:
    return _name_set('_BACK_POINTERS')


def _exact_bounds() -> frozenset[str]:
    """The facets `tree.py` emits as an exact decimal string whatever their type.

    Read from the emitter rather than restated here. A bound is a `Fraction` on
    a number and an `int` on an integer, so the annotation is right for neither
    kind, and a second copy of the list is a second thing to forget.
    """
    return _name_set('_EXACT')


def _name_set(name: str) -> frozenset[str]:
    """A module-level `frozenset({...})` of string literals in `tree.py`."""
    source = (_ROOT / 'fastraml' / 'views' / 'tree.py').read_text(encoding='utf-8')
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            call = node.value
            arguments = call.args if isinstance(call, ast.Call) else []
            return frozenset(item for argument in arguments for item in _strings(getattr(argument, 'elts', None)))
    raise LookupError(f'{name} not found in tree.py')


def _kinds() -> dict[str, list[_Facet]]:
    """Each kind's emitted facets, with the annotation each came from."""
    declared: dict[str, dict[str, str]] = {}
    for path in sorted((_ROOT / 'fastraml' / 'types').glob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.ClassDef) and node.name in _KINDS:
                declared[node.name] = _annotations(node)

    missing = [name for name in _KINDS if name not in declared]
    if missing:
        raise LookupError(f'kinds not found in fastraml/types: {", ".join(missing)}')

    back = _back_pointers()
    exact = _exact_bounds()
    out: dict[str, list[_Facet]] = {}
    for kind in _KINDS:
        facets: list[_Facet] = []
        for slot, annotation in declared[kind].items():
            if slot in _NOT_EMITTED or slot.startswith('_'):
                continue
            # A bound's spelling comes from what the emitter does with it,
            # not from what the model holds: `_EXACT` renders both the
            # `Fraction` and the `int` form as a decimal string.
            spelling = 'string' if slot in exact else 'Ref' if slot in back else _JSON_OF.get(annotation)
            if spelling is None:
                raise LookupError(f'{kind}.{slot}: no JSON spelling declared for {annotation!r} (see _JSON_OF)')
            facets.append(_Facet(slot, spelling, annotation, kind))
        out[kind] = facets
    return out


def _annotations(node: ast.ClassDef) -> dict[str, str]:
    """`self.x: T` inside `__init__`, plus any keyword-only argument that is one.

    Python keeps no runtime record of an annotation on an attribute, so this is
    the only place the type of `min_items` is written down at all.
    """
    slots = _slots(node)
    found: dict[str, str] = {}
    for statement in node.body:
        if not isinstance(statement, ast.FunctionDef) or statement.name != '__init__':
            continue
        for inner in ast.walk(statement):
            if isinstance(inner, ast.AnnAssign) and isinstance(inner.target, ast.Attribute):
                found[inner.target.attr] = ast.unparse(inner.annotation)
        for argument in statement.args.kwonlyargs:
            if argument.annotation is not None and argument.arg in slots:
                found.setdefault(argument.arg, ast.unparse(argument.annotation))
    return {slot: found[slot] for slot in slots if slot in found}


def _slots(node: ast.ClassDef) -> tuple[str, ...]:
    for statement in node.body:
        if (
            isinstance(statement, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == '__slots__' for target in statement.targets)
            and isinstance(statement.value, ast.Tuple)
        ):
            return tuple(_strings(statement.value.elts))
    return ()


# -- the hand-declared half ----------------------------------------------------

#: Which interface each `_Projector` method produces. The generator checks that
#: every method emitting literal keys is named here, so a new one cannot be
#: forgotten.
_PRODUCES: Final = {
    'model': 'Document',
    'fragment': 'EntryPoint',
    'scheme': 'SecurityScheme',
    'described': 'DescribedBy',
    'endpoint': 'Endpoint',
    'operation': 'Operation',
    'request': 'Operation',
    'response': 'Response',
    'schemes': 'SecuredBy',
    'applied': 'Applied',
    'annotation': 'DocumentAnnotation',
    'example': 'Example',
    'recursion': 'Recursion',
}

#: The type of every structural key. Only the types: which keys exist is read
#: from `tree.py`, and generation fails on a key absent from here.
_STRUCTURAL: Final[dict[str, dict[str, str]]] = {
    'Document': {
        'base': 'string',
        'entry_point': 'EntryPoint | null',
        'types': 'Record<string, Record<string, Shape>>',
        'annotation_types': 'Record<string, Record<string, Shape>>',
        'security_schemes': 'Record<string, Record<string, SecurityScheme>>',
        'endpoints': 'Record<string, Endpoint>',
        'annotations': 'DocumentAnnotation[]',
    },
    'EntryPoint': {
        'kind': 'string',
        'title': 'string',
        'version': 'string',
        'base_uri': 'string',
        'media_types': 'string[]',
        'protocols': 'string[]',
        'usage': 'string',
        'description': 'string',
        'base_uri_parameters': 'Record<string, Parameter>',
        'documentation': '{ title: string; content: string }[]',
        #: `securedBy:` at the root. Every endpoint declaring none carries the
        #: same list, so this says the API declared a *default*, not what any
        #: one endpoint requires.
        'secured_by': 'SecuredBy[]',
        'annotations': 'Applied[]',
    },
    'SecurityScheme': {
        'id': 'Address | null',
        'name': 'string',
        'type': 'string',
        'display_name': 'string',
        'description': 'string',
        'settings': 'Record<string, Json>',
        'described_by': 'DescribedBy',
        'annotations': 'Applied[]',
    },
    'DescribedBy': {
        'headers': 'Record<string, Parameter>',
        'query_parameters': 'Record<string, Parameter>',
        'query_string': 'Shape | Ref | null',
        'responses': 'Record<string, Response>',
    },
    'Endpoint': {
        'id': 'Address | null',
        'operations': 'Record<string, Operation>',
        'secured_by': 'SecuredBy[]',
        'display_name': 'string',
        'description': 'string',
        'uri_parameters': 'Record<string, Parameter>',
        'annotations': 'Applied[]',
    },
    'Operation': {
        'id': 'Address | null',
        'responses': 'Record<string, Response>',
        'description': 'string',
        'display_name': 'string',
        'protocols': 'string[]',
        'secured_by': 'SecuredBy[]',
        'annotations': 'Applied[]',
        'headers': 'Record<string, Parameter>',
        'query_parameters': 'Record<string, Parameter>',
        'query_string': 'Shape | Ref | null',
        'bodies': 'Record<string, Shape | Ref | null>',
    },
    'Response': {
        'description': 'string',
        'headers': 'Record<string, Parameter>',
        'bodies': 'Record<string, Shape | Ref | null>',
        'annotations': 'Applied[]',
    },
    'SecuredBy': {
        'name': 'string',
        'is_null': 'boolean',
        'bound': 'boolean',
        'declaration': 'Address | null',
        'scopes': 'string[] | null',
    },
    'Applied': {
        'name': 'string',
        'type': 'Address | null',
        'value': 'Json',
    },
    'DocumentAnnotation': {
        'name': 'string',
        'target': 'string',
        'type': 'Address | null',
        'value': 'Json',
    },
    'Example': {
        'value': 'Json',
        'display_name': 'string',
        'description': 'string',
        'strict': 'boolean',
        'annotations': 'Applied[]',
    },
    'Recursion': {
        'type': "'recursive'",
        'name': 'string | null',
        'head': 'Ref',
    },
}

#: The base fields `shape()` writes itself, before a kind's facets are inlined.
_SHAPE_FIELDS: Final = {
    'id': 'Address | null',
    'name': 'string | null',
    'type': 'string',
    'display_name': 'string',
    'description': 'string',
    'required': 'boolean',
    'default': 'Json',
    'example': 'Example',
    'examples': 'Record<string, Example>',
    'enum': 'Json[]',
    'xml': 'Json',
    'allowed_targets': 'string[]',
    'inherits': '(Shape | Ref)[]',
    #: Custom facet *values* -- what this type supplies. `declared_facets` is
    #: the other half: what a subtype must supply (docs/10 § 4).
    'custom_facets': 'Record<string, Json>',
    'declared_facets': 'Record<string, Property>',
    'annotations': 'Applied[]',
    'type_expr': 'string',
    #: Both only on a `json` shape, and both about the same schema: the schema
    #: itself with every reference out of it resolved, and the nearest RAML
    #: shape to it (docs/10 § 6.3).
    'json_schema': 'Json',
    'projection': 'Shape',
}

_PREAMBLE: Final = """\
/**
 * The `fastraml tree` contract.
 *
 * GENERATED by `python -m fastraml.views.bindings` -- do not edit. The key sets
 * are read from `fastraml/views/tree.py` and the facets from the kind classes in
 * `fastraml/types/`, so a facet added to a kind arrives here without this file
 * being touched. `tests/unit/test_bindings.py` fails when the two disagree.
 *
 * The metamodel is three constructs (docs/16-graph.md section 11.10):
 *
 *   {"$ref": <address>}                            a link -- look the target up
 *   {"type": "recursive", "head": {"$ref": ...}}   repeats here, do not expand
 *   anything else                                  containment -- descend
 *
 * A consumer descends containment, follows a link when it chooses to, and stops
 * at a recursion marker. It maintains no ancestor set. Requires
 * `ParseOptions(unwrap=True)`, which `fastraml tree` uses.
 */

/** A structural address: stable across re-parses, and the identity of a node. */
export type Address = string;

export type Json = string | number | boolean | null | Json[] | { [key: string]: Json };

/** A link. Its sole key is the test -- an expanded shape carries `id` as well. */
export interface Ref {
  $ref: Address;
}
"""


# -- generating ----------------------------------------------------------------


def typescript() -> str:
    """The whole declaration file."""
    emitted = _projector()
    kinds = _kinds()
    parts = [_PREAMBLE, _shape(emitted, kinds), *_interfaces(emitted), _members()]
    return '\n'.join(parts)


def _interfaces(emitted: dict[str, _Emitted]) -> list[str]:
    """One interface per structural producer, keys checked against the source."""
    written: dict[str, list[str]] = {}
    for method, interface in _PRODUCES.items():
        found = emitted.get(method)
        if found is None:
            raise LookupError(f'_PRODUCES names `{method}`, which is not a _Projector method')
        declared = _STRUCTURAL.get(interface)
        if declared is None:
            raise LookupError(f'no types declared for `{interface}` (see _STRUCTURAL)')
        lines = written.setdefault(interface, [])
        for name in found.required:
            lines.append(_field(interface, declared, name, optional=False))
        for name in found.optional:
            lines.append(_field(interface, declared, name, optional=True))

    return [
        f'export interface {interface} {{\n' + '\n'.join(dict.fromkeys(lines)) + '\n}\n'
        for interface, lines in written.items()
    ]


def _delegated(emitted: dict[str, _Emitted], caller: _Emitted) -> list[str]:
    """The literal keys the methods a caller merges in contribute.

    A `dynamic` delegate is skipped: its keys are a map read from the model, not
    a fixed set, and `kind_facets` is the one that matters -- its keys are the
    kinds' own facets and are read from the kind classes instead.
    """
    names: list[str] = []
    for method in caller.delegates:
        found = emitted.get(method)
        if found is None:
            raise LookupError(f'a projector method merges in `{method}`, which is not a _Projector method')
        if found.dynamic:
            continue
        names.extend(name for name in (*found.required, *found.optional) if name not in names)
    return names


def _field(interface: str, declared: dict[str, str], name: str, *, optional: bool) -> str:
    spelling = declared.get(name)
    if spelling is None:
        raise LookupError(
            f'{interface}.{name}: emitted by tree.py and not declared in _STRUCTURAL. Add its TypeScript type there.'
        )
    return f'  {name}{"?" if optional else ""}: {spelling};'


def _shape(emitted: dict[str, _Emitted], kinds: dict[str, list[_Facet]]) -> str:
    """`Shape`: the base fields, then every kind's facets, all optional.

    One interface rather than a discriminated union over `type`, because that is
    what the projection emits: `shape()` writes the base fields and
    `kind_facets` merges the concrete kind's onto the same object. A union would
    read better and would be a second model -- a consumer narrowing on `type`
    would have to trust a mapping this file cannot check.

    Two kinds declaring the same facet name is therefore one member, typed as
    the union of what each contributes. That is not tidying: `minimum` is a
    `number` on an integer and a `string` on a number, because a number's bound
    is a `Fraction` and passing one through `float` is what the parser exists
    not to do. Emitting the name twice would not compile; emitting one arm would
    be wrong for the other kind.
    """
    found = emitted.get('shape')
    if found is None:
        raise LookupError('_Projector.shape not found')
    # Including what it delegates to. `shape()` merges two methods' results into
    # its own: `kind_facets`, whose keys are the kinds' facets and are read
    # below, and `json_schema`, whose keys are literal. Reading only `shape()`
    # meant a key added to a delegate reached the tree and never reached this
    # file -- the one failure this generator exists to make impossible, and it
    # was silent, which is worse than the wrong type.
    delegated = _delegated(emitted, found)
    known = set(found.required) | set(found.optional) | set(delegated)
    undeclared = sorted(known - set(_SHAPE_FIELDS))
    if undeclared:
        raise LookupError(f'shape() emits {undeclared}, not declared in _SHAPE_FIELDS')

    exact = _exact_bounds()
    merged: dict[str, list[_Facet]] = {}
    for facets in kinds.values():
        for facet in facets:
            merged.setdefault(facet.name, []).append(facet)

    lines = [f'  {name}: {_SHAPE_FIELDS[name]};' for name in found.required]
    # A delegate's keys are optional whatever it says of them: whether it runs
    # at all is the caller's condition, not the delegate's.
    lines += [f'  {name}?: {_SHAPE_FIELDS[name]};' for name in [*found.optional, *delegated]]
    lines.append('\n  /* Facets, by the kind that declares each. */')
    for name, facets in sorted(merged.items()):
        spelling = ' | '.join(dict.fromkeys(facet.typescript for facet in facets))
        note = ', '.join(dict.fromkeys(facet.kind.removesuffix('Shape') for facet in facets))
        if name in exact:
            note += ' -- an exact decimal, e.g. "0.01" or "1.7976931348623157E+308"'
        lines.append(f'  {name}?: {spelling}; // {note}')
    body = '\n'.join(lines)
    return (
        '/**\n'
        ' * A type, of any of the seventeen kinds.\n'
        ' *\n'
        ' * Flat rather than a union over `type` because that is how it arrives: the\n'
        " * base fields and the concrete kind's facets are merged onto one object.\n"
        ' *\n'
        ' * A bound on a number -- `minimum`, `maximum`, `multipleOf` -- arrives as\n'
        ' * an exact decimal in a **string**, on every kind that declares one.\n'
        ' * JSON`s number is a double in every consumer that matters, and the parser\n'
        ' * never passes a number through `float` on either side of a comparison; a\n'
        ' * bound written as a JSON number undoes that at the last step, which is\n'
        ' * how `maximum: 9223372036854775807` came back out of `JSON.parse` as\n'
        ' * ...808. A *count* -- `minLength`, `maxItems` -- is bounded by memory and\n'
        ' * stays a number.\n'
        ' */\n'
        f'export interface Shape {{\n{body}\n}}\n'
    )


def _members() -> str:
    """The three small records `value()` produces for a container's members."""
    return (
        'export interface Property {\n'
        '  required: boolean;\n'
        '  type: Shape | Ref | null;\n'
        '}\n\n'
        'export interface PatternProperty {\n'
        '  pattern: string;\n'
        '  type: Shape | Ref | null;\n'
        '}\n\n'
        'export interface Parameter {\n'
        '  binding: string;\n'
        '  required: boolean;\n'
        '  type: Shape | Ref | null;\n'
        '}\n'
    )


def main() -> None:
    path = _ROOT / DESTINATION
    path.write_text(typescript(), encoding='utf-8')
    sys.stdout.write(f'wrote {path}\n')


if __name__ == '__main__':
    main()

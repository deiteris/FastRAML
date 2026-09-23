"""Language-neutral schema of the ``fastraml tree`` wire contract.

The schema reads the emitter and model source once. Backends consume its plain
records and decide only how those facts are represented in their target
language; no backend needs to understand Python's AST.
"""

from __future__ import annotations

import ast
import enum
import pathlib
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    'JSON_ONLY',
    'PRODUCES',
    'Container',
    'ContractSchema',
    'Emitted',
    'Facet',
    'Holds',
    'ShapeKind',
    'Structural',
    'Vocabulary',
    'contract_schema',
]

_ROOT: Final = pathlib.Path(__file__).resolve().parent.parent.parent.parent

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
        'pending_facets',
        'link',
        'alias',
        'is_annotation_type',
        'shape',
        'from_mapping',
        'validator',
        'location',
    }
)


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """One closed wire vocabulary, independent of a target language."""

    name: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ShapeKind:
    """One emitted shape discriminator and the model class implementing it."""

    name: str
    model: str


@dataclass(frozen=True, slots=True)
class Emitted:
    """What one ``_Projector`` method puts in its result."""

    required: tuple[str, ...]
    optional: tuple[str, ...]
    delegates: tuple[str, ...]
    dynamic: bool


@dataclass(frozen=True, slots=True)
class Facet:
    """One kind-specific field and how the wire encodes its value."""

    name: str
    annotation: str
    wire_form: Literal['annotation', 'exact_decimal', 'reference']


class Holds(enum.StrEnum):
    """What one key's leaf value is, said without reference to a type system."""

    CONSTANT = 'constant'
    SCALAR = 'scalar'
    VOCABULARY = 'vocabulary'
    JSON = 'json'
    #: Any of the metamodel's three constructs (docs/16 § 6.1).
    SHAPE_NODE = 'shape_node'
    #: An expanded shape and nothing else -- `projection`.
    SHAPE = 'shape'
    #: A link and nothing else -- `head`.
    REF = 'ref'
    RECORD = 'record'


class Container(enum.StrEnum):
    """How many of them, and under what kind of key."""

    ONE = 'one'
    LIST = 'list'
    #: Keys are data: a media type, a status, a declaration name.
    MAP = 'map'
    #: By file, then by declaration name.
    MAP_OF_MAP = 'map_of_map'


@dataclass(frozen=True, slots=True)
class Structural:
    """What one structural key holds, in no particular language.

    No source read produces this: `out['operations']` is an expression, and
    nothing recovers its type. It is declared once instead of three times: a backend turns this into a spelling,
    and the walk generator reads the same record to find where shapes live.
    """

    holds: Holds
    #: The record, vocabulary or scalar domain named, where one applies. For a
    #: constant, the value itself as the contract writes it.
    of: str = ''
    container: Container = Container.ONE
    #: The named domain of a map's keys, where it has one.
    key: str = ''
    #: The contract's own name for the whole spelling. All three backends use
    #: these names, so the alias is a fact about the contract, not a language.
    alias: str = ''
    #: The key is present and its value may be null. Not the same as the key
    #: being absent, which is what `Emitted.optional` says.
    nullable: bool = False
    #: For `CONSTANT`, the value itself. A `str` or an `int`, so a backend
    #: decides its own literal syntax instead of parsing one out of `of`.
    constant: str | int | None = None


@dataclass(frozen=True, slots=True)
class ContractSchema:
    """All source-derived facts shared by language backends."""

    projector: dict[str, Emitted]
    shape_kinds: tuple[ShapeKind, ...]
    shape_facets: dict[str, tuple[Facet, ...]]
    vocabularies: tuple[Vocabulary, ...]
    exact_decimal_slots: frozenset[str]
    #: Every fixed record, by name, and what each of its keys holds.
    structural: dict[str, dict[str, Structural]]

    def structure_of(self, record: str, key: str) -> Structural:
        """What `record.key` holds, or a `LookupError` naming the omission."""
        declared = self.structural.get(record)
        if declared is None:
            raise LookupError(f'no structure declared for `{record}` (see _STRUCTURE in schema.py)')
        found = declared.get(key)
        if found is None:
            raise LookupError(
                f'{record}.{key}: emitted by tree.py and not declared in schema.py. Say what it holds in _STRUCTURE.'
            )
        return found

    def produced_keys(self) -> Iterator[tuple[str, str, bool]]:
        """`(record, key, optional)` for every structural producer in `PRODUCES`.

        Required keys first, then optional ones, producer by producer: the order
        every backend declares them in.
        """
        for method, record in PRODUCES.items():
            found = self.projector.get(method)
            if found is None:
                raise LookupError(f'PRODUCES names `{method}`, which is not a _Projector method')
            for key in found.required:
                yield record, key, False
            for key in found.optional:
                yield record, key, True

    def shape_projection(self) -> tuple[Emitted, tuple[str, ...]]:
        """`_Projector.shape` and the keys its delegates merge in, checked.

        Including what it delegates to. `shape()` merges two methods' results
        into its own: `kind_facets`, whose keys are the kinds' facets, and
        `json_schema`, whose keys are literal. Reading only `shape()` meant a
        key added to a delegate reached the tree and never reached a generated
        file -- the one failure the generators exist to make impossible, and it
        was silent, which is worse than the wrong type.
        """
        found = self.projector.get('shape')
        if found is None:
            raise LookupError('_Projector.shape not found')
        delegated = self.delegated_fields(found)
        known = set(found.required) | set(found.optional) | set(delegated)
        undeclared = sorted(known - set(self.structural['ShapeBase']))
        if undeclared:
            raise LookupError(f"shape() emits {undeclared}, not declared under 'ShapeBase' in schema.py")
        return found, delegated

    def kinds_by_model(self) -> dict[str, list[str]]:
        """Each model class, and the discriminator values it implements, in order."""
        by_model: dict[str, list[str]] = {}
        for kind in self.shape_kinds:
            by_model.setdefault(kind.model, []).append(kind.name)
        return by_model

    def facet_structure(self, facet: Facet) -> Structural:
        """What a kind-specific facet holds, from its model annotation.

        Derived rather than tabulated: the annotation already says whether the
        slot holds a shape, and a facet added to a kind must not need a second
        edit here to be walked.
        """
        if facet.wire_form == 'exact_decimal':
            return Structural(Holds.SCALAR, 'ExactDecimal')
        if facet.wire_form == 'reference':
            return Structural(Holds.REF)
        found = _FACET_STRUCTURE.get(facet.annotation)
        if found is None:
            raise LookupError(f'no structure declared for the annotation {facet.annotation!r} (see _FACET_STRUCTURE)')
        return found

    def shape_bearing(self) -> dict[str, dict[str, Structural]]:
        """Every record's shape-bearing keys, with records that reach none pruned.

        The walk table, before any language sees it. Each backend renders this
        as data its runtime half reads; none of them decides what is in it.

        Pruned transitively: `Example` holds `annotations`, which holds
        `Applied`, which holds no shape, so neither appears. Without the closure
        the table would carry every record in the contract to describe the four
        that matter.
        """
        records = {record: dict(keys) for record, keys in self.structural.items()}
        # Each kind carries only what it adds. Every shape is a `ShapeBase`, so
        # a walk reads that entry too and the table stops repeating three rows
        # sixteen times.
        for kind in self.shape_kinds:
            records[kind.model] = {facet.name: self.facet_structure(facet) for facet in self.shape_facets[kind.model]}
        # `projection` is declared where the types declare it. Leaving it on
        # `ShapeBase` costs nothing in a language that reads a table by string
        # key and fails to compile in one that does not -- which is how Go found
        # it.
        json_only = {key: records['ShapeBase'].pop(key) for key in JSON_ONLY if key in records['ShapeBase']}
        records['JsonShape'].update(json_only)

        # A record bears shapes if it holds one directly, or holds a record that
        # does. Iterate to a fixed point; the graph is small and cyclic (a
        # Property holds a node, which is a shape, which holds Properties).
        direct = {Holds.SHAPE_NODE, Holds.SHAPE}
        bears = {name for name, keys in records.items() if any(s.holds in direct for s in keys.values())}
        while True:
            grown = {
                name
                for name, keys in records.items()
                if any(s.holds is Holds.RECORD and s.of in bears for s in keys.values())
            }
            if grown <= bears:
                break
            bears |= grown

        out: dict[str, dict[str, Structural]] = {}
        for name, keys in records.items():
            kept = {
                key: s for key, s in keys.items() if s.holds in direct or (s.holds is Holds.RECORD and s.of in bears)
            }
            if kept:
                out[name] = kept
        return out

    def delegated_fields(self, caller: Emitted) -> tuple[str, ...]:
        """Literal fields merged into ``caller`` by non-dynamic delegates."""
        names: list[str] = []
        for method in caller.delegates:
            found = self.projector.get(method)
            if found is None:
                raise LookupError(f'a projector method merges in `{method}`, which is not a _Projector method')
            if found.dynamic:
                continue
            names.extend(name for name in (*found.required, *found.optional) if name not in names)
        return tuple(names)


#: What a kind-specific facet holds, by the model annotation the AST read finds.
#: A facet naming `BaseShape` is a place a shape sits, and that is the whole of
#: what the walk needs; a backend's spelling table is consulted separately.
_FACET_STRUCTURE: Final[dict[str, Structural]] = {
    'ScalarFacet[int] | None': Structural(Holds.SCALAR, 'int'),
    'ScalarFacet[str] | None': Structural(Holds.SCALAR, 'str'),
    'ScalarFacet[bool] | None': Structural(Holds.SCALAR, 'bool'),
    'ScalarFacet[Fraction] | None': Structural(Holds.SCALAR, 'ExactDecimal'),
    'ScalarFacet[re.Pattern[str]] | None': Structural(Holds.SCALAR, 'str'),
    'list[ScalarFacet[str]] | None': Structural(Holds.SCALAR, 'str', container=Container.LIST),
    # `| None` on the model slot is the slot being unset, which the wire says by
    # leaving the key out -- not by a null. `Emitted.optional` carries that.
    'BaseShape | None': Structural(Holds.SHAPE_NODE),
    'list[BaseShape] | None': Structural(Holds.SHAPE_NODE, container=Container.LIST),
    'dict[str, Property] | None': Structural(Holds.RECORD, 'Property', container=Container.MAP),
    'dict[str, PatternProperty] | None': Structural(Holds.RECORD, 'PatternProperty', container=Container.MAP),
    'DataNode | None': Structural(Holds.JSON),
}

#: The two keys only a `json` shape carries. Both are whole documents about the
#: same schema, and the spec forbids a JSON-schema type from taking part in
#: inheritance, so neither belongs to every shape. Declared under `ShapeBase`
#: because `shape()` is what writes them, and moved onto `JsonShape` wherever a
#: backend or a walk asks which record holds them.
JSON_ONLY: Final = frozenset({'json_schema', 'projection'})

#: Which record each `_Projector` method produces. One mapping, not three: the
#: method-to-record correspondence is a fact about the projection, and a backend
#: that disagreed about it would declare a record nothing emits.
PRODUCES: Final[dict[str, str]] = {
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
}

_STR: Final = Structural(Holds.SCALAR, 'str')
_BOOL: Final = Structural(Holds.SCALAR, 'bool')
_JSON: Final = Structural(Holds.JSON)
_NODE: Final = Structural(Holds.SHAPE_NODE, nullable=True)
_ID: Final = Structural(Holds.SCALAR, 'Address', nullable=True)
_APPLIED: Final = Structural(Holds.RECORD, 'Applied', container=Container.LIST)
_SECURED: Final = Structural(Holds.RECORD, 'SecuredBy', container=Container.LIST)
_PARAMETERS: Final = Structural(Holds.RECORD, 'Parameter', container=Container.MAP)
_RESPONSES: Final = Structural(
    Holds.RECORD, 'Response', container=Container.MAP, key='StatusCode', alias='ResponsesByStatus'
)
#: The alias carries the element's nullability -- `dict[MediaType, ShapeNode | None]`.
#: A body key with no type is a body the author declared and gave no shape.
_BODIES: Final = Structural(Holds.SHAPE_NODE, container=Container.MAP, key='MediaType', alias='BodiesByMediaType')

#: Every fixed record and what each key holds. The one hand-written table the
#: contract needs, and the reason it is here rather than in a backend: three
#: languages spell `dict[str, Parameter]` three ways and agree completely on
#: what it *is*.
#:
#: The records declared in each `static/` half are here too. A walk crosses them
#: -- `properties` holds `Property`, which holds a node -- so leaving them out
#: would put the recursion back in every consumer, which is the thing this is
#: for.
_STRUCTURE: Final[dict[str, dict[str, Structural]]] = {
    'Document': {
        'format': Structural(Holds.CONSTANT, constant='fastraml-tree'),
        'format_version': Structural(Holds.CONSTANT, constant=1),
        'view': Structural(Holds.CONSTANT, constant='effective'),
        'base': Structural(Holds.SCALAR, 'Address'),
        'entry_point': Structural(Holds.RECORD, 'EntryPoint', nullable=True),
        'types': Structural(
            Holds.SHAPE_NODE, container=Container.MAP_OF_MAP, key='DeclarationName', alias='ShapeDeclarationsByFile'
        ),
        'annotation_types': Structural(
            Holds.SHAPE_NODE, container=Container.MAP_OF_MAP, key='DeclarationName', alias='ShapeDeclarationsByFile'
        ),
        'security_schemes': Structural(
            Holds.RECORD,
            'SecurityScheme',
            container=Container.MAP_OF_MAP,
            key='DeclarationName',
            alias='SecuritySchemeDeclarationsByFile',
        ),
        'endpoints': Structural(
            Holds.RECORD, 'Endpoint', container=Container.MAP, key='EndpointPath', alias='EndpointsByPath'
        ),
        'annotations': Structural(Holds.RECORD, 'DocumentAnnotation', container=Container.LIST),
    },
    'EntryPoint': {
        'kind': Structural(Holds.VOCABULARY, 'FragmentKind'),
        'title': _STR,
        'version': _STR,
        'base_uri': _STR,
        'media_types': Structural(Holds.SCALAR, 'str', container=Container.LIST),
        'protocols': Structural(Holds.VOCABULARY, 'Protocol', container=Container.LIST),
        'usage': _STR,
        'description': _STR,
        'base_uri_parameters': _PARAMETERS,
        'documentation': Structural(Holds.RECORD, 'DocumentationItem', container=Container.LIST),
        #: `securedBy:` at the root. Every endpoint declaring none carries the
        #: same list, so this says the API declared a *default*, not what any
        #: one endpoint requires.
        'secured_by': _SECURED,
        'annotations': _APPLIED,
    },
    'SecurityScheme': {
        'id': _ID,
        'name': _STR,
        'type': Structural(Holds.SCALAR, 'SecuritySchemeType', alias='SecuritySchemeType'),
        'display_name': _STR,
        'description': _STR,
        'settings': Structural(Holds.JSON, container=Container.MAP, alias='SecuritySettings'),
        'described_by': Structural(Holds.RECORD, 'DescribedBy'),
        'annotations': _APPLIED,
    },
    'DescribedBy': {
        'headers': _PARAMETERS,
        'query_parameters': _PARAMETERS,
        'query_string': _NODE,
        'responses': _RESPONSES,
    },
    'Endpoint': {
        'id': _ID,
        'operations': Structural(
            Holds.RECORD, 'Operation', container=Container.MAP, key='HttpMethod', alias='OperationsByMethod'
        ),
        'secured_by': _SECURED,
        'display_name': _STR,
        'description': _STR,
        'uri_parameters': _PARAMETERS,
        'annotations': _APPLIED,
    },
    'Operation': {
        'id': _ID,
        'responses': _RESPONSES,
        'description': _STR,
        'display_name': _STR,
        'protocols': Structural(Holds.VOCABULARY, 'Protocol', container=Container.LIST),
        'secured_by': _SECURED,
        'annotations': _APPLIED,
        'headers': _PARAMETERS,
        'query_parameters': _PARAMETERS,
        'query_string': _NODE,
        'bodies': _BODIES,
    },
    'Response': {
        'description': _STR,
        'headers': _PARAMETERS,
        'bodies': _BODIES,
        'annotations': _APPLIED,
    },
    'SecuredBy': {
        'name': _STR,
        'is_null': _BOOL,
        'bound': _BOOL,
        'declaration': _ID,
        'scopes': Structural(Holds.SCALAR, 'str', container=Container.LIST, nullable=True),
    },
    'Applied': {
        'name': _STR,
        'type': _ID,
        'value': _JSON,
    },
    'DocumentAnnotation': {
        'name': _STR,
        'target': Structural(Holds.VOCABULARY, 'AnnotationTarget'),
        'type': _ID,
        'value': _JSON,
    },
    'Example': {
        'value': _JSON,
        'display_name': _STR,
        'description': _STR,
        'strict': _BOOL,
        'annotations': _APPLIED,
    },
    #: The fields `shape()` writes itself, before a kind's facets are inlined.
    'ShapeBase': {
        'id': _ID,
        'name': Structural(Holds.SCALAR, 'str', nullable=True),
        'type': Structural(Holds.VOCABULARY, 'ShapeType'),
        'display_name': _STR,
        'description': _STR,
        'required': _BOOL,
        'default': _JSON,
        'example': Structural(Holds.RECORD, 'Example'),
        'examples': Structural(Holds.RECORD, 'Example', container=Container.MAP),
        'enum': Structural(Holds.JSON, container=Container.LIST),
        'xml': _JSON,
        'allowed_targets': Structural(Holds.VOCABULARY, 'AnnotationTarget', container=Container.LIST),
        'inherits': Structural(Holds.SHAPE_NODE, container=Container.LIST),
        #: Custom facet *values* -- what this type supplies. `declared_facets` is
        #: the other half: what a subtype must supply (docs/10 § 4).
        'custom_facets': Structural(Holds.JSON, container=Container.MAP),
        'declared_facets': Structural(Holds.RECORD, 'Property', container=Container.MAP),
        'annotations': _APPLIED,
        'type_expr': _STR,
        #: Both only on a `json` shape, and both about the same schema: the
        #: schema itself with every reference out of it resolved, and the
        #: nearest RAML shape to it (docs/10 § 7).
        'json_schema': _JSON,
        'projection': Structural(Holds.SHAPE),
        #: A recursion marker's back pointer. Written through a loop over
        #: `_BACK_POINTERS`, so no AST read sees the key.
        'head': Structural(Holds.REF),
    },
    # -- the records each `static/` half declares ------------------------------
    'DocumentationItem': {'title': _STR, 'content': _STR},
    'Property': {'required': _BOOL, 'type': _NODE},
    'PatternProperty': {'pattern': _STR, 'type': _NODE},
    'Parameter': {
        'binding': Structural(Holds.VOCABULARY, 'ParameterBinding'),
        'required': _BOOL,
        'type': _NODE,
    },
}


@cache
def contract_schema() -> ContractSchema:
    """Read and cache the complete language-neutral contract."""
    kinds = _shape_kinds()
    exact = _name_set('_EXACT')
    back = _name_set('_BACK_POINTERS')
    return ContractSchema(
        projector=_projector(),
        shape_kinds=kinds,
        shape_facets=_shape_facets(kinds, exact, back),
        structural=_STRUCTURE,
        vocabularies=(
            Vocabulary('ShapeType', tuple(kind.name for kind in kinds)),
            Vocabulary('HttpMethod', _set_values('fastraml/parser/source_ir.py', 'METHODS')),
            Vocabulary(
                'FragmentKind',
                _mapped_enum_values('fastraml/parser/fragments.py', '_FRAGMENT_CLASSES', 'FragmentKind'),
            ),
            Vocabulary('AnnotationTarget', _enum_values('fastraml/domains.py', 'DomainLocation')),
        ),
        exact_decimal_slots=exact,
    )


def _projector() -> dict[str, Emitted]:
    source = _tree('fastraml/views/tree.py')
    projector = next(node for node in ast.walk(source) if isinstance(node, ast.ClassDef) and node.name == '_Projector')
    return {
        method.name: _emitted(method)
        for method in projector.body
        if isinstance(method, ast.FunctionDef) and not method.name.startswith('__')
    }


def _emitted(method: ast.FunctionDef) -> Emitted:
    required: list[str] = []
    optional: list[str] = []
    delegates: list[str] = []
    dynamic = False
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
    return Emitted(
        required=tuple(dict.fromkeys(required)),
        optional=tuple(name for name in dict.fromkeys(optional) if name not in at_hand),
        delegates=tuple(dict.fromkeys(delegates)),
        dynamic=dynamic,
    )


def _writes(method: ast.FunctionDef) -> Iterator[tuple[ast.AST, bool]]:
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
    return [
        inner.func.attr
        for argument in node.args
        for inner in ast.walk(argument)
        if isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Attribute)
        and isinstance(inner.func.value, ast.Name)
        and inner.func.value.id == 'self'
    ]


def _shape_facets(
    shape_kinds: tuple[ShapeKind, ...], exact: frozenset[str], back: frozenset[str]
) -> dict[str, tuple[Facet, ...]]:
    kinds = tuple(dict.fromkeys(kind.model for kind in shape_kinds))
    declared: dict[str, dict[str, str]] = {}
    for path in sorted((_ROOT / 'fastraml' / 'types').glob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.ClassDef) and node.name in kinds:
                declared[node.name] = _annotations(node)
    missing = [name for name in kinds if name not in declared]
    if missing:
        raise LookupError(f'kinds not found in fastraml/types: {", ".join(missing)}')

    out: dict[str, tuple[Facet, ...]] = {}
    for kind in kinds:
        facets = []
        for slot, annotation in declared[kind].items():
            if slot in _NOT_EMITTED or slot.startswith('_'):
                continue
            wire_form: Literal['annotation', 'exact_decimal', 'reference'] = (
                'exact_decimal' if slot in exact else 'reference' if slot in back else 'annotation'
            )
            facets.append(Facet(slot, annotation, wire_form))
        out[kind] = tuple(facets)
    return out


def _annotations(node: ast.ClassDef) -> dict[str, str]:
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


def _shape_kinds() -> tuple[ShapeKind, ...]:
    value = _assigned('fastraml/types/shape.py', 'KIND_TO_CLASS')
    if not isinstance(value, ast.Dict):
        raise TypeError('fastraml/types/shape.py:KIND_TO_CLASS is not a mapping literal')
    return tuple(
        ShapeKind(key.value, model.id)
        for key, model in zip(value.keys, value.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and isinstance(model, ast.Name)
    )


def _name_set(name: str) -> frozenset[str]:
    for node in ast.walk(_tree('fastraml/views/tree.py')):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            call = node.value
            arguments = call.args if isinstance(call, ast.Call) else []
            return frozenset(item for argument in arguments for item in _strings(getattr(argument, 'elts', None)))
    raise LookupError(f'{name} not found in tree.py')


@cache
def _tree(path: str) -> ast.Module:
    return ast.parse((_ROOT / path).read_text(encoding='utf-8'))


def _assigned(path: str, name: str) -> ast.expr:
    for node in _tree(path).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets) and node.value is not None:
                return node.value
    raise LookupError(f'{name} not found in {path}')


def _set_values(path: str, name: str) -> tuple[str, ...]:
    value = _assigned(path, name)
    if not isinstance(value, ast.Call) or not value.args or not isinstance(value.args[0], (ast.Set, ast.Tuple)):
        raise TypeError(f'{path}:{name} is not a set literal')
    values = [
        item.value for item in value.args[0].elts if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]
    return tuple(sorted(values))


def _enum_values(path: str, name: str) -> tuple[str, ...]:
    return tuple(_enum_members(path, name).values())


def _enum_members(path: str, name: str) -> dict[str, str]:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                statement.targets[0].id: statement.value.value
                for statement in node.body
                if isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            }
    raise LookupError(f'{name} not found in {path}')


def _mapped_enum_values(path: str, mapping: str, enum: str) -> tuple[str, ...]:
    value = _assigned(path, mapping)
    if not isinstance(value, ast.Dict):
        raise TypeError(f'{path}:{mapping} is not a mapping literal')
    members = _enum_members(path, enum)
    names = [key.attr for key in value.keys if isinstance(key, ast.Attribute) and isinstance(key.value, ast.Name)]
    return tuple(members[name] for name in names)


def _strings(elements: object) -> list[str]:
    if not isinstance(elements, list):
        return []
    return [item.value for item in elements if isinstance(item, ast.Constant) and isinstance(item.value, str)]

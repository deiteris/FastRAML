"""One class per node kind — `docs/16-graph.md` § 2.7.

A node's kind is its class. `TypeNode` holds a `BaseShape`, `ResponseNode` holds
a `Response`, and the entity's type is a parameter of the class rather than a
value checked against it, so a node whose kind and entity disagree does not
typecheck and cannot be built.

`attributes` is a method on each. Every key it yields is a name § 2 owns —
`additionalProperties`, `statusCode`, `definedIn` — and the model spells each of
them differently. Translating between the two is what this module is for: the
projection owns the vocabulary, not the values, so nothing here is stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, ClassVar, Final

from pyraml.parser.directives import DirectiveRef, SecurityScheme
from pyraml.parser.endpoints import Body, EndPoint, Operation, Request, Response
from pyraml.parser.fragments import APIFragment, Fragment
from pyraml.parser.resourcetypes import ResourceTypeDefinition
from pyraml.parser.security import SecuritySchemeDefinition
from pyraml.parser.traits import TraitDefinition
from pyraml.types.base import BaseShape, Parameter, PatternProperty, Property, ScalarFacet
from pyraml.types.jsonschema_ import projected
from pyraml.uris import relative_to

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyraml.positions import Position

__all__ = [
    'ApiNode',
    'DeclaredNode',
    'EndPointNode',
    'Entity',
    'GraphNode',
    'Literal_',
    'OperationNode',
    'ParameterNode',
    'PatternPropertyNode',
    'PayloadNode',
    'PropertyNode',
    'RequestNode',
    'ResourceTypeNode',
    'ResponseNode',
    'SecuritySchemeNode',
    'TraitNode',
    'TypeNode',
    'UnitNode',
    'UnresolvedNode',
    'UnresolvedResourceTypeNode',
    'UnresolvedSchemeNode',
    'UnresolvedTraitNode',
]

#: What a node attribute may be. A tuple is a genuinely multi-valued facet —
#: `enum`, OAuth scopes — and is not joined into a string: an enum value may
#: itself contain a space, so `["new york", "london"]` and three separate values
#: would be indistinguishable, and a diff could not say which member was removed.
type Literal_ = str | int | bool | tuple[str, ...]

#: Everything a node can stand for. Every node holds one; a node standing for
#: nothing would be something this layer invented (docs/16 § 1).
type Entity = (
    BaseShape
    | Property
    | PatternProperty
    | Parameter
    | Body
    | Request
    | Response
    | Operation
    | EndPoint
    | Fragment
    | TraitDefinition
    | ResourceTypeDefinition
    | SecuritySchemeDefinition
    | DirectiveRef
    | SecurityScheme
)

#: The model's binding names to this vocabulary's. They differ in one place —
#: RAML declares `uriParameters` and the graph spells that `path`, in the
#: attribute and in the IRI segment. The model's name is its own.
_BINDING: Final[dict[str, str]] = {'uri': 'path', 'query': 'query', 'header': 'header'}


@dataclass(slots=True, eq=False)
class GraphNode[E: Entity]:
    """One entity, projected.

    `kinds` is most-specific first, as AMF's `@type` arrays are, so a consumer
    that wants one label reads `kinds[0]` and one that wants to filter reads the
    rest.
    """

    iri: str
    #: The model object this node projects.
    entity: E
    #: The entry document's directory URI, which `definedIn` is relative to.
    #: The one piece of context the entity does not carry; every node in a graph
    #: holds the same string object.
    root: str = ''

    #: This vocabulary's name for what the node is.
    kind: ClassVar[str] = ''

    @property
    def kinds(self) -> tuple[str, ...]:
        return (self.kind,)

    @property
    def attributes(self) -> dict[str, Literal_]:
        """The node's literals, read from the entity.

        A fresh dictionary per call, so a caller reading it more than once binds
        it to a local.
        """
        return {}

    def __repr__(self) -> str:
        return f'{type(self).__name__}({self.iri!r})'


# -- types ---------------------------------------------------------------------


@dataclass(slots=True, eq=False)
class TypeNode(GraphNode[BaseShape]):
    """A declaration: its common facets, then whatever its kind holds."""

    #: The shape class, which `kinds[1]` reports: `ObjectShape`, `ArrayShape`…
    shape_kind: str = 'UnknownShape'

    kind: ClassVar[str] = 'Type'

    @property
    def kinds(self) -> tuple[str, ...]:
        return (self.kind, self.shape_kind)

    @property
    def attributes(self) -> dict[str, Literal_]:
        base = self.entity
        found = _drop(
            {
                'name': base.name,
                'type': base.type,
                'displayName': _text(base.display_name),
                'description': _text(base.description),
                'required': _facet_value(base.required.value) if base.required is not None else None,
                'isAnnotationType': base.is_annotation_type or None,
            }
        )
        found.update(_where(base.location, base.key_pos, self.root))
        # Facets come from the *projected* shape, so a type defined by a JSON
        # schema has them rather than reading as a leaf (docs/16 § 2.6).
        # `as_shape` caches, so asking twice costs one dictionary build.
        view = projected(base)
        shape = view.shape
        if shape is not None:
            # Read off the instance rather than a per-kind table, so a facet
            # added to a kind appears without this module being touched. One of
            # the two places `getattr` is required: the attribute name comes
            # from `_slots`, and the `isinstance` recovers the type.
            for name in _slots(type(shape)):
                value = getattr(shape, name, None)
                if isinstance(value, ScalarFacet):
                    literal = _facet_value(value.value)
                    if literal is not None:
                        found[_camel(name)] = literal
        if view.enum is not None:
            # `DataNode.raw` is the plain Python value already.
            found['enum'] = tuple(str(member.raw) for member in view.enum)
        return found


@dataclass(slots=True, eq=False)
class PropertyNode(GraphNode[Property]):
    kind: ClassVar[str] = 'Property'

    @property
    def attributes(self) -> dict[str, Literal_]:
        return {'name': self.entity.name, 'required': self.entity.required}


@dataclass(slots=True, eq=False)
class PatternPropertyNode(GraphNode[PatternProperty]):
    kind: ClassVar[str] = 'PatternProperty'

    @property
    def attributes(self) -> dict[str, Literal_]:
        written = self.entity.pattern.pattern
        return {'name': written, 'pattern': written}


# -- endpoints -----------------------------------------------------------------


@dataclass(slots=True, eq=False)
class EndPointNode(GraphNode[EndPoint]):
    kind: ClassVar[str] = 'EndPoint'

    @property
    def attributes(self) -> dict[str, Literal_]:
        endpoint = self.entity
        named = _named(
            {'path': endpoint.full_uri}, _text(endpoint.display_name) or endpoint.full_uri, endpoint.description
        )
        return named | _where(endpoint.location, endpoint.key_pos, self.root)


@dataclass(slots=True, eq=False)
class OperationNode(GraphNode[Operation]):
    """A method, and what its security amounts to.

    No `path`: the endpoint holds it and `supportedOperation` reaches it, and a
    fact reachable by following an edge is not an attribute (docs/16 § 2.8).
    """

    kind: ClassVar[str] = 'Operation'

    @property
    def attributes(self) -> dict[str, Literal_]:
        operation = self.entity
        found = _named(
            {'method': operation.method}, _text(operation.display_name) or operation.method, operation.description
        )
        found.update(_where(operation.location, operation.key_pos, self.root))
        # Every scheme in force, not the last one seen: an operation may be
        # secured by two OAuth schemes and narrow the scopes of both.
        scopes = tuple(
            scope for scheme in operation.secured_by if not scheme.is_null for scope in scheme.compiled_params or ()
        )
        if scopes:
            found['scopes'] = scopes
        if any(scheme.is_null for scheme in operation.secured_by):
            # `securedBy: [null]` *removes* inherited security (docs/09 § A3).
            # It is a fact about the method, so it is recorded on the method
            # rather than dropped for having no scheme to point at.
            found['unsecured'] = True
        return found


@dataclass(slots=True, eq=False)
class RequestNode(GraphNode[Request]):
    """Somewhere to hang parameters and payloads. Its edges say the rest."""

    kind: ClassVar[str] = 'Request'


@dataclass(slots=True, eq=False)
class ResponseNode(GraphNode[Response]):
    kind: ClassVar[str] = 'Response'

    @property
    def attributes(self) -> dict[str, Literal_]:
        response = self.entity
        named = _named(
            {'statusCode': response.code}, _text(response.display_name) or response.code, response.description
        )
        return named | _where(response.location, response.key_pos, self.root)


@dataclass(slots=True, eq=False)
class PayloadNode(GraphNode[Body]):
    kind: ClassVar[str] = 'Payload'

    @property
    def attributes(self) -> dict[str, Literal_]:
        body = self.entity
        media: dict[str, Literal_] = {'mediaType': body.media_type} if body.media_type else {}
        return media | _where(body.location, body.key_pos, self.root)


@dataclass(slots=True, eq=False)
class ParameterNode(GraphNode[Parameter]):
    """A URI, query or header parameter.

    A node of its own rather than an edge straight to the type, because
    `required` and the binding belong to the *use* and not to the type: the same
    declared type is a required path parameter here and an optional header
    there. `Parameter` says which, so nothing here has to be told.
    """

    kind: ClassVar[str] = 'Parameter'

    @property
    def attributes(self) -> dict[str, Literal_]:
        param = self.entity
        bound: dict[str, Literal_] = {
            'name': param.name,
            'binding': _BINDING[param.binding],
            'required': param.required,
        }
        return bound | _where(param.base.location, param.key_pos, self.root)


# -- documents -----------------------------------------------------------------


@dataclass(slots=True, eq=False)
class ApiNode(GraphNode[APIFragment]):
    """The entry document as an API.

    Its `Unit` node is the same object as a file, which is why a node's kind is
    its class and not a function of the entity.
    """

    kind: ClassVar[str] = 'Api'

    @property
    def attributes(self) -> dict[str, Literal_]:
        api = self.entity
        return _drop(
            {
                'name': _text(api.title),
                'version': _text(api.version),
                'description': _text(api.description),
                'baseUri': _text(api.base_uri),
            }
        )


@dataclass(slots=True, eq=False)
class UnitNode(GraphNode[Fragment]):
    """One file that holds a declaration, of any fragment kind.

    No `definedIn`: its name already is its path from the root.
    """

    kind: ClassVar[str] = 'Unit'

    @property
    def attributes(self) -> dict[str, Literal_]:
        return {'name': relative_to(self.entity.location, self.root)}


# -- declarations a name can reach ---------------------------------------------


@dataclass(slots=True, eq=False)
class DeclaredNode[E: TraitDefinition | ResourceTypeDefinition | SecuritySchemeDefinition](GraphNode[E]):
    """A declaration a `type:`, `is:` or `securedBy:` entry can name.

    All three say the same two things about themselves, and both matter to a
    reader: the name it is applied by, and the file it is written in, which is
    rarely the file it is applied in.
    """

    @property
    def attributes(self) -> dict[str, Literal_]:
        return {'name': self.entity.name} | _where(self.entity.location, self.entity.key_pos, self.root)


@dataclass(slots=True, eq=False)
class TraitNode(DeclaredNode[TraitDefinition]):
    kind: ClassVar[str] = 'Trait'


@dataclass(slots=True, eq=False)
class ResourceTypeNode(DeclaredNode[ResourceTypeDefinition]):
    kind: ClassVar[str] = 'ResourceType'


@dataclass(slots=True, eq=False)
class SecuritySchemeNode(DeclaredNode[SecuritySchemeDefinition]):
    kind: ClassVar[str] = 'SecurityScheme'

    @property
    def attributes(self) -> dict[str, Literal_]:
        scheme = self.entity
        found: dict[str, Literal_] = {'name': scheme.name}
        if scheme.type:
            found['type'] = scheme.type
        return found | _where(scheme.location, scheme.key_pos, self.root)


@dataclass(slots=True, eq=False)
class UnresolvedNode[E: DirectiveRef | SecurityScheme](GraphNode[E]):
    """A reference whose name matched no declaration.

    An edge is emitted anyway, so an application is never invisible; the node it
    points at has a name and nothing else, because nothing else is known.
    """

    @property
    def attributes(self) -> dict[str, Literal_]:
        return {'name': self.entity.name} if self.entity.name else {}


@dataclass(slots=True, eq=False)
class UnresolvedTraitNode(UnresolvedNode[DirectiveRef]):
    kind: ClassVar[str] = 'Trait'


@dataclass(slots=True, eq=False)
class UnresolvedResourceTypeNode(UnresolvedNode[DirectiveRef]):
    kind: ClassVar[str] = 'ResourceType'


@dataclass(slots=True, eq=False)
class UnresolvedSchemeNode(UnresolvedNode[DirectiveRef | SecurityScheme]):
    kind: ClassVar[str] = 'SecurityScheme'


# -- shared readers ------------------------------------------------------------


def _named(found: dict[str, Literal_], name: str, description: ScalarFacet[str] | None) -> dict[str, Literal_]:
    """`found`, plus the two things almost every kind says about itself."""
    if name:
        found['name'] = name
    text = _text(description)
    if text is not None:
        found['description'] = text
    return found


def _where(location: str, position: Position | None, root: str) -> dict[str, Literal_]:
    """Where an entity was written, spelled the way the vocabulary spells it."""
    found: dict[str, Literal_] = {'definedIn': relative_to(location, root)}
    if position is not None and position.is_known:
        found['line'] = position.line
        found['column'] = position.column
    return found


def _drop(found: dict[str, Literal_ | None]) -> dict[str, Literal_]:
    """A facet that is absent says nothing rather than saying `None`."""
    return {key: value for key, value in found.items() if value is not None}


def _slots(cls: type) -> Iterator[str]:
    """Every `__slots__` entry down the MRO.

    `__slots__` is not declared by any base class in the hierarchy, `object`
    does not have it, and the names are what the walk is *for*.
    """
    for klass in cls.__mro__:
        yield from getattr(klass, '__slots__', ())


def _camel(name: str) -> str:
    head, _, rest = name.partition('_')
    return head + ''.join(part.title() for part in rest.split('_') if part)


def _text(facet: ScalarFacet[str] | None) -> str | None:
    return facet.value if facet is not None and facet.value else None


def _facet_value(value: object) -> str | int | bool | None:
    """One `ScalarFacet`'s value as a literal.

    `object` rather than a union: the facets are `ScalarFacet[T]` for seven
    different `T`, they are reached through the untyped `__slots__` walk above,
    and the point of this function is to be the one place that decides what an
    unrecognised `T` becomes.
    """
    if value is True or value is False:
        return value
    if isinstance(value, Fraction):
        return _number_text(value)
    if isinstance(value, (int, str)):
        return value
    return None


def _number_text(value: Fraction) -> str:
    """A `Fraction` as text, without going through `float`.

    The project rule is that numbers never pass through `float` on either side
    of a comparison (docs/10 § 5.2), and a graph literal is no exception even
    though nothing compares it: `1.1` reaching a reader as `1.100000000000000088`
    would be a defect of this module, not of the parser.
    """
    if value.denominator == 1:
        return str(value.numerator)
    residue = value.denominator
    for factor in (2, 5):
        while residue % factor == 0:
            residue //= factor
    if residue != 1:
        # Not representable as a terminating decimal, so it is reported exactly
        # as the ratio it is. `multipleOf: 1/3` cannot arise from RAML source,
        # which is decimal, but a merged bound could in principle.
        return f'{value.numerator}/{value.denominator}'
    digits = 0
    scaled = value
    while scaled.denominator != 1:
        scaled *= 10
        digits += 1
    text = str(abs(scaled.numerator)).rjust(digits + 1, '0')
    sign = '-' if scaled.numerator < 0 else ''
    return f'{sign}{text[:-digits]}.{text[-digits:]}'

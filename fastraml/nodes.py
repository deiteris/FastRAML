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

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.parser.directives import DirectiveRef, SecurityScheme
from fastraml.parser.endpoints import Body, EndPoint, Operation, Request, Response
from fastraml.parser.fragments import APIFragment, Fragment
from fastraml.parser.resourcetypes import ResourceTypeDefinition
from fastraml.parser.security import SecuritySchemeDefinition
from fastraml.parser.traits import TraitDefinition
from fastraml.types.base import BaseShape, Parameter, PatternProperty, Property, ScalarFacet, facets_of
from fastraml.types.jsonschema_ import projected
from fastraml.types.values import decimal_text
from fastraml.uris import relative_to

if TYPE_CHECKING:
    from fastraml.positions import Position

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
#: would be indistinguishable, and a comparison could not say which member left.
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
    def name(self) -> str:
        """The node's authored name, or `''` where it has none.

        Separate from `attributes` because looking a name up is the commonest
        single-key read there is — `Graph.find` does it once per node and
        `label` once per node pair — and `attributes` is far too expensive to
        serve it. Building a `TypeNode`'s dictionary projects the shape, walks
        every facet its kind declares and relativises its path, all of which is
        discarded when the caller wanted one string: **12x slower than reading
        the entity** over 36 510 nodes, 30.1 ms against 2.6 ms (docs/12 § 19e).

        Each override is the same expression `attributes` uses for its `name`
        key, and `attributes` reads this property rather than repeating it, so
        the two cannot drift. `tests/unit/test_graph.py` asserts they agree for
        every node kind.
        """
        return ''

    @property
    def attributes(self) -> dict[str, Literal_]:
        """The node's literals, read from the entity.

        A fresh dictionary per call, so a caller reading it more than once binds
        it to a local. Where only the name is wanted, read `name`.
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
    def name(self) -> str:
        return self.entity.name or ''

    @property
    def attributes(self) -> dict[str, Literal_]:
        base = self.entity
        found = _drop(
            {
                'name': self.name,
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
            # `facets_of` is shared with `render`, so the two views cannot
            # disagree about what a kind constrains (docs/14 § 2). Only the
            # conversion to a literal is this module's.
            for name, facet in facets_of(shape):
                literal = _facet_value(facet.value)
                if literal is not None:
                    found[name] = literal
        if view.enum is not None:
            # `DataNode.raw` is the plain Python value already.
            found['enum'] = tuple(str(member.raw) for member in view.enum)
        return found


@dataclass(slots=True, eq=False)
class PropertyNode(GraphNode[Property]):
    kind: ClassVar[str] = 'Property'

    @property
    def name(self) -> str:
        return self.entity.name

    @property
    def attributes(self) -> dict[str, Literal_]:
        return {'name': self.name, 'required': self.entity.required}


@dataclass(slots=True, eq=False)
class PatternPropertyNode(GraphNode[PatternProperty]):
    kind: ClassVar[str] = 'PatternProperty'

    @property
    def name(self) -> str:
        return self.entity.pattern.pattern

    @property
    def attributes(self) -> dict[str, Literal_]:
        written = self.name
        return {'name': written, 'pattern': written}


# -- endpoints -----------------------------------------------------------------


@dataclass(slots=True, eq=False)
class EndPointNode(GraphNode[EndPoint]):
    kind: ClassVar[str] = 'EndPoint'

    @property
    def name(self) -> str:
        return _text(self.entity.display_name) or self.entity.full_uri

    @property
    def attributes(self) -> dict[str, Literal_]:
        endpoint = self.entity
        named = _named({'path': endpoint.full_uri}, self.name, endpoint.description)
        return named | _where(endpoint.location, endpoint.key_pos, self.root)


@dataclass(slots=True, eq=False)
class OperationNode(GraphNode[Operation]):
    """A method, and what its security amounts to.

    No `path`: the endpoint holds it and `supportedOperation` reaches it, and a
    fact reachable by following an edge is not an attribute (docs/16 § 2.8).
    """

    kind: ClassVar[str] = 'Operation'

    @property
    def name(self) -> str:
        return _text(self.entity.display_name) or self.entity.method

    @property
    def attributes(self) -> dict[str, Literal_]:
        operation = self.entity
        found = _named({'method': operation.method}, self.name, operation.description)
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
    def name(self) -> str:
        return _text(self.entity.display_name) or self.entity.code

    @property
    def attributes(self) -> dict[str, Literal_]:
        response = self.entity
        named = _named({'statusCode': response.code}, self.name, response.description)
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
    def name(self) -> str:
        return self.entity.name

    @property
    def attributes(self) -> dict[str, Literal_]:
        param = self.entity
        bound: dict[str, Literal_] = {
            'name': self.name,
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
    def name(self) -> str:
        return _text(self.entity.title) or ''

    @property
    def attributes(self) -> dict[str, Literal_]:
        api = self.entity
        return _drop(
            {
                'name': self.name,
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
    def name(self) -> str:
        return relative_to(self.entity.location, self.root)

    @property
    def attributes(self) -> dict[str, Literal_]:
        return {'name': self.name}


# -- declarations a name can reach ---------------------------------------------


@dataclass(slots=True, eq=False)
class DeclaredNode[E: TraitDefinition | ResourceTypeDefinition | SecuritySchemeDefinition](GraphNode[E]):
    """A declaration a `type:`, `is:` or `securedBy:` entry can name.

    All three say the same two things about themselves, and both matter to a
    reader: the name it is applied by, and the file it is written in, which is
    rarely the file it is applied in.
    """

    @property
    def name(self) -> str:
        return self.entity.name

    @property
    def attributes(self) -> dict[str, Literal_]:
        return {'name': self.name} | _where(self.entity.location, self.entity.key_pos, self.root)


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
        found: dict[str, Literal_] = {'name': self.name}
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
    def name(self) -> str:
        return self.entity.name or ''

    @property
    def attributes(self) -> dict[str, Literal_]:
        return {'name': self.name} if self.name else {}


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


def _text(facet: ScalarFacet[str] | None) -> str | None:
    return facet.value if facet is not None and facet.value else None


def _facet_value(value: object) -> str | int | bool | None:
    """One `ScalarFacet`'s value as a literal.

    `object` rather than a union: the facets are `ScalarFacet[T]` for seven
    different `T`, `facets_of` reaches them through an untyped `__slots__` walk,
    and the point of this function is to be the one place that decides what an
    unrecognised `T` becomes.
    """
    if value is True or value is False:
        return value
    if isinstance(value, Fraction):
        return decimal_text(value)
    if isinstance(value, re.Pattern):
        # The facet holds a *compiled* pattern. Without this a `pattern:` on a
        # type reaches no node attribute at all, so every consumer of the
        # projection is blind to one being tightened -- the § 2.6 failure in a
        # different place.
        return str(value.pattern)
    if isinstance(value, (int, str)):
        return value
    return None

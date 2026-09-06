"""The effective document as an addressed tree — docs/16-graph.md § 11.

The graph answers *what points at what*; this answers *what is here*. Both are
views of the same model and neither is a filter of the other: the graph carries
identity and reference and drops leaf data nothing points at, and a tree carries
the data and collapses identity into nesting. They are lossy on orthogonal axes
(§ 4).

**Which is why references here are addresses, not names.** A tree hits a
cross-reference at four places — recursion, `inherits`, an alias, an applied
annotation — and a name is not an identity: two libraries may each declare
`paged`, and an anonymous shape has no name to write. Every reference below is
an address from the same `Walk` the graph used, so a node in one output and the
same entity in the other are joinable (`pyraml.walk`).

Positions are projected separately by `positions_of`. A one-line edit to a
document shifts every position after it, and a view that churned on every edit
would be read as noise (docs/14 § 2).

Requires `ParseOptions(unwrap=True)`: this is the effective document, so the
addresses and the contents are both of the unwrapped model.
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import TYPE_CHECKING

from pyraml.datanode import DataNode, ValueNode
from pyraml.parser.fragments import DataTypeFragment
from pyraml.types.base import BaseShape, Parameter, PatternProperty, Property, ScalarFacet, copyable_slots
from pyraml.types.examples import Example, Examples
from pyraml.walk import DEFAULT_BASE, Addresses, address
from pyraml.yamlnode import Node, NodeKind

if TYPE_CHECKING:
    from pyraml.parser.annotations import DomainExtension
    from pyraml.parser.directives import SecurityScheme
    from pyraml.parser.endpoints import Body, EndPoint, Operation, Request, Response
    from pyraml.parser.fragments import Fragment
    from pyraml.parser.security import SecuritySchemeDefinition, SecuritySchemeDescription
    from pyraml.positions import Position
    from pyraml.registry import Raml

__all__ = ['Json', 'effective', 'positions_of']

#: What this module emits. Everything is a JSON value, so the result goes
#: through `json.dumps` without an encoder and through any consumer without one.
type Json = str | int | float | bool | list[Json] | dict[str, Json] | None

#: Fields carrying a source position, an object identity or a back-pointer.
#: None of them is stable across an edit, and none describes what the model
#: *means*. `id` is here because it is a per-parse counter: the address that
#: replaces it is structural and so survives a re-parse.
_SKIP = frozenset(
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
    }
)


def effective(raml: Raml, *, addresses: Addresses | None = None, base: str = DEFAULT_BASE) -> Json:
    """The whole parse, as a value `json.dumps` accepts.

    Pass `addresses` to reuse a map already assigned — `Graph.addresses`, say.
    The walk that assigns them is most of the cost, and a consumer holding both
    views should pay for it once. `base` is used only when one is built here.
    """
    if addresses is None:
        addresses = address(raml, base=base)
    return _Projector(addresses, _declared(raml)).model(raml)


def _typed_fragment(raml: Raml) -> tuple[str, str, BaseShape] | None:
    """The `#%RAML 1.0 DataType` entry document, if that is what was parsed.

    A typed fragment is one declaration, and `fragment_types` lists it only when
    some document's `types:` included it. As the *entry point* nothing lists it,
    so reading only `fragment_types` projected a document whose entire content
    is a type as having none — silently, since an empty map is what a document
    with no types looks like.

    The entry point alone, because that is the only case nothing else covers.
    An included fragment is already listed under the name that included it, and
    the graph gives its shape an address *under* that declaration
    (`…/types/User/inherits/user.raml`) rather than a top-level one — so adding
    a top-level entry here would invent a declaration the graph does not have.
    """
    entry = raml.entry_point
    if not isinstance(entry, DataTypeFragment) or entry.shape is None:
        return None
    location = entry.location
    return location, entry.shape.name or location.rsplit('/', 1)[-1], entry.shape


def _declared(raml: Raml) -> frozenset[int]:
    """Every shape the document declares under a name.

    What decides whether a supertype is *referenced* or *inlined*: a declaration
    is listed under `types`, so pointing at it loses nothing, and an anonymous
    one exists nowhere else in the tree, so pointing at it would lose it
    entirely. The address infix cannot answer this — a nested anonymous shape
    inside a declaration carries `#/declarations/` too.
    """
    named = [
        shape
        for declared in (*raml.fragment_types.values(), *raml.fragment_annotations.values())
        for shape in declared.values()
        if shape is not None
    ]
    entry = _typed_fragment(raml)
    if entry is not None:
        named.append(entry[2])
    return frozenset(shape.id for shape in named)


def positions_of(raml: Raml) -> Json:
    """Every declared shape's span, keyed by file and name. Projected apart.

    Only the cases that are *about* positions carry one of these, so an edit to
    any other document cannot churn it.
    """
    out: dict[str, Json] = {}
    for uri, declared in raml.fragment_types.items():
        if declared:
            out[_relative(raml, uri)] = {
                name: {'key': _position(base.key_pos), 'value': _position(base.value_pos)}
                for name, base in declared.items()
            }
    entry = _typed_fragment(raml)
    if entry is not None:
        uri, name, shape = entry
        out[_relative(raml, uri)] = {name: {'key': _position(shape.key_pos), 'value': _position(shape.value_pos)}}
    return out


def _relative(raml: Raml, uri: str) -> str:
    """A URI as a path relative to the entry point's directory.

    Absolute paths differ per machine and per temporary directory, so a view
    that kept them would be unreadable and unrepeatable.
    """
    root = raml.location.rsplit('/', 1)[0] + '/'
    return uri.removeprefix(root)


def _position(position: Position | None) -> Json:
    return None if position is None else [position.line, position.column]


class _Projector:
    """One projection, holding the addresses every reference is written with."""

    __slots__ = ('addresses', 'declared')

    def __init__(self, addresses: Addresses, declared: frozenset[int]) -> None:
        self.addresses = addresses
        self.declared = declared

    # -- references -----------------------------------------------------------

    def at(self, entity_id: int) -> str | None:
        """The address of `entity_id`, or `None` where the walk never reached it.

        `None` rather than a synthesised address: an entity the walk did not
        reach is one nothing can point at, and inventing an address for it would
        produce a reference that resolves nowhere — the failure this module
        exists to remove.
        """
        return self.addresses.of.get(entity_id)

    def reference(self, base: BaseShape, seen: frozenset[int]) -> Json:
        """A supertype or alias target: `$ref` when it is a declaration, inline
        when it is not.

        A declaration is under `types` in this same document, so a reference to
        it loses nothing and repeating it would make one type read differently
        depending on which subtype you arrived through. An anonymous supertype
        — the `integer` in `type: integer | number` after P9 distributes a facet
        — is in no other part of the tree, so referencing it would drop it.
        Both forms carry the address: `$ref` is the whole value, and an inlined
        shape carries its own `id`.
        """
        if base.id in self.declared:
            return {'$ref': self.at(base.id)}
        return self.shape(base, seen)

    # -- the model ------------------------------------------------------------

    def model(self, raml: Raml) -> Json:
        return {
            'base': self.addresses.base,
            'entry_point': self.fragment(raml.entry_point),
            'types': self.types(raml),
            'security_schemes': self.security_schemes(raml),
            'endpoints': {path: self.endpoint(endpoint) for path, endpoint in raml.endpoints.items()},
            'annotations': [self.annotation(extension) for extension in raml.domain_extensions],
        }

    def security_schemes(self, raml: Raml) -> Json:
        """Every declared scheme, by the file it was written in.

        A section of its own rather than repeated at each `securedBy:`. The
        settings and `describedBy` belong to the declaration, and a use site
        already points at it by address — the rule § 11.3 applies to a supertype.
        """
        out: dict[str, Json] = {}
        for location, fragment in raml.fragments.items():
            declared = getattr(fragment, 'security_schemes', None)
            if declared:
                out[_relative(raml, location)] = {
                    name: self.scheme(definition) for name, definition in declared.items()
                }
        return out

    def scheme(self, definition: SecuritySchemeDefinition) -> Json:
        """One `securitySchemes:` entry, with what a caller has to satisfy.

        `settings` and `describedBy` are the point: the OAuth 2.0 URLs, grants
        and scopes on one side, and the headers and query parameters a request
        must carry on the other. Without them a reader knows a scheme is
        required and nothing about how to satisfy it.
        """
        out: dict[str, Json] = {
            'id': self.at(definition.id),
            'name': definition.name,
            'type': definition.type,
        }
        for field in ('display_name', 'description'):
            value = getattr(definition, field, None)
            if value is not None:
                out[field] = self.value(value, frozenset())
        settings = definition.settings
        if settings is not None:
            # `values` holds the scalars and `lists` the sequences, so both have
            # to be read; either alone drops half of an OAuth 2.0 declaration.
            spelled: dict[str, Json] = {name: self.value(facet, frozenset()) for name, facet in settings.values.items()}
            spelled.update({name: [*items] for name, items in settings.lists.items()})
            if spelled:
                out['settings'] = spelled
        if definition.described_by is not None:
            out['described_by'] = self.described(definition.described_by)
        if definition.annotations:
            out['annotations'] = self.applied_to(definition.annotations)
        return out

    def described(self, described: SecuritySchemeDescription) -> Json:
        """What `describedBy:` says a secured request and response carry."""
        out: dict[str, Json] = {}
        for field in ('headers', 'query_parameters'):
            declared = getattr(described, field, None)
            if declared:
                out[field] = {name: self.value(param, frozenset()) for name, param in declared.items()}
        if described.query_string is not None:
            out['query_string'] = self.shape(described.query_string)
        if described.responses:
            out['responses'] = {str(code): self.response(r) for code, r in described.responses.items()}
        return out

    def types(self, raml: Raml) -> Json:
        """Every declaration, by the file it was written in.

        Typed fragments are folded in under their own location: one is a
        declaration that no `types:` block need mention.
        """
        out: dict[str, Json] = {}
        for uri, declared in raml.fragment_types.items():
            if declared:
                out[_relative(raml, uri)] = {name: self.shape(base) for name, base in declared.items()}
        entry = _typed_fragment(raml)
        if entry is not None:
            uri, name, shape = entry
            out[_relative(raml, uri)] = {name: self.shape(shape)}
        return out

    # -- shapes ---------------------------------------------------------------

    def shape(self, base: BaseShape | None, seen: frozenset[int] = frozenset()) -> Json:  # noqa: PLR0912 - one branch per optional facet
        """One declaration, with its kind's own facets inlined.

        `seen` closes recursion. A self-referential type is a cycle in the model
        by design (docs/07 § 4), so this has to be finite by construction rather
        than by hoping the input is a tree. The cycle is closed with an
        **address**: `{'$ref': …}` is followable, where the type's name was not
        and an anonymous type had none to give.
        """
        if base is None:
            return None
        if base.id in seen:
            return {'$ref': self.at(base.id)}
        seen = seen | {base.id}

        out: dict[str, Json] = {'id': self.at(base.id), 'name': base.name, 'type': base.type}
        if base.shape is not None:
            out['kind'] = type(base.shape).__name__
        for field in ('display_name', 'description', 'required', 'is_annotation_type'):
            value = getattr(base, field, None)
            if value is not None:
                out[field] = self.value(value, seen)
        for field in ('default', 'example', 'examples', 'enum', 'xml', 'allowed_targets'):
            value = getattr(base, field, None)
            if value is not None:
                out[field] = self.value(value, seen)
        if base.inherits:
            # Addresses, never names: `inherits: [Base]` in two libraries that
            # both declare `Base` is two different supertypes, and a name cannot
            # say which.
            parents: list[Json] = [self.reference(parent, seen) for parent in base.inherits]
            out['inherits'] = parents
        if base.link is not None:
            out['link'] = type(base.link).__name__
        if base.alias is not None:
            out['alias_of'] = self.reference(base.alias, seen)
        if base.custom_facets:
            out['custom_facets'] = {name: self.value(node, seen) for name, node in base.custom_facets.items()}
        if base.custom_facet_defs:
            declared: list[Json] = [*sorted(base.custom_facet_defs)]
            out['declares_facets'] = declared
        if base.annotations:
            applied: list[Json] = [self.applied(name, extension) for name, extension in base.annotations.items()]
            out['annotations'] = applied
        if base.type_expr:
            out['type_expr'] = self.value(base.type_expr, seen)
        if base.shape is not None:
            out.update(self.kind_facets(base.shape, seen))
        return out

    def kind_facets(self, shape: object, seen: frozenset[int]) -> dict[str, Json]:
        """Every field the concrete kind declares, skipping the unset ones.

        Driven off `__slots__`, using the same `copyable_slots` walk `clone`
        uses, for the reason docs/05 § 1 gives there: `__slots__` on every model
        class is a project rule rather than a convention, so the field list
        cannot go stale. A facet added to a kind and not wired in here would
        otherwise be invisible — the failure a complete view exists to prevent,
        so the view must not have it.

        Wider than `facets_of`, which yields only the `ScalarFacet` constraints:
        this also has to carry the containers — `properties`, `items`, `any_of`
        — because a tree places its members rather than pointing at them.
        """
        out: dict[str, Json] = {}
        for name in copyable_slots(type(shape)):
            if name in _SKIP or name.startswith('__'):
                continue
            value = getattr(shape, name, None)
            if value is None or value in ([], {}):
                continue
            out[name] = self.value(value, seen)
        return out

    # -- values ---------------------------------------------------------------

    def value(self, value: object, seen: frozenset[int]) -> Json:  # noqa: PLR0911, PLR0912 - one arm per model type
        if isinstance(value, Node):
            return _node(value)
        if isinstance(value, BaseShape):
            return self.shape(value, seen)
        if isinstance(value, ScalarFacet):
            return self.value(value.value, seen)
        if isinstance(value, Parameter):
            return {'binding': value.binding, 'required': value.required, 'type': self.shape(value.base, seen)}
        if isinstance(value, Property):
            return {'required': value.required, 'type': self.shape(value.base, seen)}
        if isinstance(value, PatternProperty):
            return {'pattern': value.pattern.pattern, 'type': self.shape(value.base, seen)}
        if isinstance(value, DataNode):
            return _plain(value.value)
        if isinstance(value, ValueNode):
            return _plain(value)
        if isinstance(value, re.Pattern):
            return str(value.pattern)
        if isinstance(value, Fraction):
            return str(value)
        if isinstance(value, Examples):
            # `entries()`, never `values`: with `examples: !include e.raml` the
            # examples live on the fragment and `values` is empty, so reading it
            # does not fail — it silently sees nothing.
            return {name: self.value(example, seen) for name, example in value.entries().items()}
        if isinstance(value, Example):
            return self.value(value.data, seen)
        if isinstance(value, dict):
            return {str(key): self.value(item, seen) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.value(item, seen) for item in value]
        return _plain(value)

    # -- fragments, endpoints, annotations ------------------------------------

    def fragment(self, fragment: Fragment | None) -> Json:
        if fragment is None:
            return None
        out: dict[str, Json] = {'kind': type(fragment).__name__}
        for field in ('title', 'version', 'base_uri', 'media_types', 'protocols', 'usage', 'description'):
            value = getattr(fragment, field, None)
            if value is not None:
                out[field] = self.value(value, frozenset())
        declared = getattr(fragment, 'base_uri_parameters', None)
        if declared:
            # `{tenant}` in the base URI is a value every caller has to supply,
            # so a reader who cannot see it cannot build a request at all.
            out['base_uri_parameters'] = {name: self.value(param, frozenset()) for name, param in declared.items()}
        items = getattr(fragment, 'documentation', None)
        if items:
            out['documentation'] = [
                {'title': self.value(item.title, frozenset()), 'content': self.value(item.content, frozenset())}
                for item in items
            ]
        annotations = getattr(fragment, 'annotations', None)
        if annotations:
            out['annotations'] = self.applied_to(annotations)
        return out

    def endpoint(self, endpoint: EndPoint) -> Json:
        out: dict[str, Json] = {
            'id': self.at(endpoint.id),
            'operations': {method: self.operation(op) for method, op in (endpoint.operations or {}).items()},
            'secured_by': self.schemes(endpoint.secured_by),
        }
        # A resource carries the prose a navigation pane is built from, and it
        # reached no view here at all: an operation had both, its resource had
        # neither.
        for field in ('display_name', 'description'):
            value = getattr(endpoint, field, None)
            if value is not None:
                out[field] = self.value(value, frozenset())
        if endpoint.uri_parameters:
            out['uri_parameters'] = {
                name: self.value(param, frozenset()) for name, param in endpoint.uri_parameters.items()
            }
        if endpoint.annotations:
            out['annotations'] = self.applied_to(endpoint.annotations)
        return out

    def operation(self, operation: Operation) -> Json:
        """A method, with its parameters' **shapes** and not merely their names.

        The names alone were what the first draft projected, and it made the
        `collection-merge-enum` case — which exists to pin the spec's own
        `[mac, unix, win]` — assert nothing at all: the merged `enum` lives on
        the query parameter's shape. A view that omits the thing its case is
        named after is worse than none, because it looks like cover.
        """
        out: dict[str, Json] = {
            'id': self.at(operation.id),
            'responses': {str(code): self.response(r) for code, r in (operation.responses or {}).items()},
        }
        if operation.description is not None:
            out['description'] = self.value(operation.description, frozenset())
        if operation.display_name is not None:
            out['display_name'] = self.value(operation.display_name, frozenset())
        if operation.secured_by:
            out['secured_by'] = self.schemes(operation.secured_by)
        if operation.annotations:
            out['annotations'] = self.applied_to(operation.annotations)
        request = operation.request
        if request is not None:
            out.update(self.request(request))
        return out

    def request(self, request: Request) -> dict[str, Json]:
        out: dict[str, Json] = {}
        for field in ('headers', 'query_parameters'):
            declared = getattr(request, field, None)
            if declared:
                out[field] = {name: self.value(param, frozenset()) for name, param in declared.items()}
        if request.query_string is not None:
            out['query_string'] = self.shape(request.query_string)
        if request.bodies:
            out['bodies'] = {media: self.body(body) for media, body in request.bodies.items()}
        return out

    def response(self, response: Response) -> Json:
        out: dict[str, Json] = {}
        if response.description is not None:
            out['description'] = self.value(response.description, frozenset())
        if response.headers:
            out['headers'] = {name: self.value(param, frozenset()) for name, param in response.headers.items()}
        if response.bodies:
            out['bodies'] = {media: self.body(body) for media, body in response.bodies.items()}
        if response.annotations:
            out['annotations'] = self.applied_to(response.annotations)
        return out

    def body(self, body: Body) -> Json:
        return self.shape(body.shape)

    def schemes(self, schemes: list[SecurityScheme] | None) -> Json:
        """A `securedBy:` list, with the fields that carry its meaning.

        `is_null` is `securedBy: [null]` — how an author *removes* an inherited
        scheme (docs/09 § A3). It binds to a real definition of type `null`
        rather than to nothing, so a view that only kept names would render it
        as a scheme called "null" and lose the distinction from one actually
        named that.

        `scopes` is OAuth 2.0's narrowed scope list. An earlier draft read a
        `scopes` attribute that does not exist, so every narrowing projected as
        `[]` — `getattr` with a default turning a wrong field name into a
        plausible empty answer.
        """
        return [
            {
                'name': scheme.name,
                'is_null': scheme.is_null,
                # Both, because they answer different questions. A `[null]`
                # entry binds to a synthesised definition that no document
                # declares, so it is `bound` with no address — and reporting
                # only the address would make it read as unbound.
                'bound': scheme.definition is not None,
                'declaration': self.at(scheme.definition.id) if scheme.definition is not None else None,
                # `None` is "no narrowing was recorded" and `[]` is "narrowed
                # to nothing". Collapsing them loses the OAuth 2.0 case the
                # `secured-by-null` document exists to pin.
                'scopes': None if scheme.compiled_params is None else [*scheme.compiled_params],
            }
            for scheme in schemes or []
            if scheme is not None
        ]

    def applied_to(self, annotations: dict[str, DomainExtension]) -> Json:
        """Every annotation applied at one site, each pointing at its type.

        On the site, not only in the document-wide list. There `target` is a
        *kind* — `Resource` — so a reader could see that something was
        deprecated and not what.
        """
        return [self.applied(name, extension) for name, extension in annotations.items()]

    def applied(self, name: str, extension: DomainExtension) -> Json:
        """One applied annotation, pointing at the annotation *type*.

        The name alone was what this held, and a name cannot say which of two
        libraries declaring `deprecated` was meant.
        """
        defined_by = extension.defined_by
        return {
            'name': name,
            'type': self.at(defined_by.id) if defined_by is not None else None,
        }

    def annotation(self, extension: DomainExtension) -> Json:
        return {
            'name': extension.name,
            'target': str(getattr(extension, 'target', '') or ''),
            'type': self.at(extension.defined_by.id) if extension.defined_by is not None else None,
            'value': self.value(extension.value, frozenset()) if extension.value is not None else None,
        }


def _node(node: Node) -> Json:
    """A YAML node as its text, never its `repr`.

    Two fields hold raw nodes — `type_expr` and the `pending_facets` a union or
    an unknown kind could not digest — and a node's `repr` carries its source
    position. Projecting that would put a line number into every view that
    touches a type expression, which is the churn `positions_of` exists to keep
    out.
    """
    if node.kind is NodeKind.SCALAR:
        return node.value
    if node.kind is NodeKind.SEQUENCE:
        return [_node(item) for item in node.content]
    return {node.content[i].value: _node(node.content[i + 1]) for i in range(0, len(node.content) - 1, 2)}


def _plain(value: object) -> Json:  # noqa: PLR0911 - one arm per leaf type
    if isinstance(value, ValueNode):
        # Exactly one of the three applies, and a scalar `None` is a legitimate
        # YAML null — so the mapping and sequence are tested first rather than
        # falling back on `scalar` being None.
        #
        # Through `entries` and `items`, which are what those containers hold. A
        # `MappingValue` is not a mapping and a `SequenceValue` is not iterable;
        # treating them as though they were raised on the first structured
        # annotation value to reach here, and nothing had.
        if value.mapping is not None:
            return {entry.key: _plain(entry.value) for entry in value.mapping.entries}
        if value.sequence is not None:
            return [_plain(item.value) for item in value.sequence.items]
        return _plain(value.scalar)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)

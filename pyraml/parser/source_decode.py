"""Stage 2 — materialize the endpoint IR into the model.

Each retained tree is decoded **exactly once**, which is the whole point of the
two-stage split (docs/08 section 2). By the time this runs, Phase 6's merge has
finished rearranging the IR, so what arrives here is the final branch.

Every shape this creates is registered with `Raml.put_typedef`. That is the seam
that makes P9 and P10 reach a body, a header or a query parameter without either
pass knowing endpoints exist — `unwrap_shapes` and `validate_shapes` both
iterate `fragment_typedefs` and nothing else.

The other half of what runs here is the provenance overlay (docs/08 section
6.3). A merged body holds nodes authored in up to three files, so the decode
pushes a scope per boundary root, and every entity constructor asks
`Raml.location_of` which file its node came from. Without that, a type name a
trait contributed resolves in the applying document's namespace — and still
parses, which is why this is the phase's characteristic silent failure.

See docs/08-templates-and-endpoints.md sections 3, 6 and 8.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

from pyraml.domains import DomainLocation
from pyraml.errors import Accumulator, RamlError
from pyraml.parser.annotations import is_annotation_key, unmarshal_domain_extension
from pyraml.parser.endpoints import Body, EndPoint, Operation, Request, Response
from pyraml.parser.facets import make_string_facet, scalar_str
from pyraml.types.shape import make_body_shape, make_property_map, make_shape
from pyraml.yamlnode import NodeKind, is_null, node_error, pairs

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pyraml.parser.annotations import DomainExtension
    from pyraml.parser.source_ir import SourceEndPoint, SourceOperation
    from pyraml.registry import Raml
    from pyraml.yamlnode import Node

__all__ = ['decode_source_endpoint']

FACET_DISPLAY_NAME: Final = 'displayName'
FACET_DESCRIPTION: Final = 'description'
FACET_HEADERS: Final = 'headers'
FACET_QUERY_PARAMETERS: Final = 'queryParameters'
FACET_QUERY_STRING: Final = 'queryString'
FACET_RESPONSES: Final = 'responses'
FACET_BODY: Final = 'body'
FACET_PROTOCOLS: Final = 'protocols'
FACET_URI_PARAMETERS: Final = 'uriParameters'


@contextmanager
def _body_scope(raml: Raml, source: SourceEndPoint | SourceOperation) -> Iterator[None]:
    """Decode this unit's body in the namespace it belongs to.

    Normally that is the unit's own declaration scope. But the body root may
    itself be a provenance boundary — an operation with no body of its own,
    whose entire body was grafted from a trait — and then the trait's scope is
    the base every unmarked node beneath it inherits.
    """
    scope = source.scope
    if source.body is not None:
        scope = source.provenance.get(source.body, scope)
    if scope is None:
        yield
        return
    raml.push_ctx(scope)
    try:
        yield
    finally:
        raml.pop_ctx()


def _annotation(raml: Raml, into: dict[str, DomainExtension], key: Node, value: Node, location: str) -> None:
    extension = unmarshal_domain_extension(raml, location, key, value)
    into[extension.name] = extension


def _protocols(node: Node, location: str) -> list[str]:
    if node.kind is not NodeKind.SEQUENCE:
        raise node_error('protocols must be a sequence', location, node)
    return [scalar_str(item, location).upper() for item in node.content]


# -- bodies and media types (docs/08 section 8.3) ------------------------------


def _is_media_type_map(node: Node) -> bool:
    """A `body:` whose keys are media types rather than facets.

    The test is the slash, as in the reference implementation: every RFC 6838
    media type has one and no RAML facet name does.
    """
    if node.kind is not NodeKind.MAPPING:
        return False
    keys = [key for key, _ in pairs(node)]
    return bool(keys) and all('/' in key.value for key in keys)


def _decode_bodies(raml: Raml, node: Node, location: str, target: DomainLocation) -> dict[str, Body]:
    """`body:` in either spelling (docs/08 section 8.3)."""
    if is_null(node):
        return {}
    location = raml.location_of(node, location)
    bodies: dict[str, Body] = {}

    if _is_media_type_map(node):
        # The media-type node *is* the body node the spec's target table names,
        # so an annotation written inside one targets RequestBody/ResponseBody.
        with raml.target_scope(target):
            for key, value in pairs(node):
                shape = make_body_shape(raml, key, value, location)
                raml.put_typedef(shape.location, shape)
                bodies[key.value] = Body(
                    id=raml.next_id(),
                    media_type=key.value,
                    location=location,
                    shape=shape,
                    key_pos=key.position,
                    value_pos=value.full_position,
                )
        return bodies

    # A `body:` written without media-type keys *is* a type declaration, and an
    # annotation inside it targets `TypeDeclaration` rather than the body — the
    # spec's table calls RequestBody/ResponseBody "the body node", which in the
    # media-type spelling is the node above. `Annotations/complex-01/valid.raml`
    # pins the distinction and the two `target-locations/valid-*-body` fixtures
    # pin the other side of it.
    with raml.target_scope(DomainLocation.TYPE_DECLARATION):
        mixed = [key.value for key, _ in pairs(node) if '/' in key.value] if node.kind is NodeKind.MAPPING else []
        if mixed:
            # `body: {application/json: ..., type: Foo}` — the common mistake,
            # and one that reads as though it should work.
            raise node_error('body mixes media types with facets', location, node, info={'mediaTypes': sorted(mixed)})
        if not raml.global_media_types:
            raise node_error('explicit media type is required', location, node)

        # One declaration, instantiated once per default media type. The shape
        # is built per media type too: they are separate declarations as far as
        # P7 and P10 are concerned, and sharing one would alias their facets.
        for media_type in raml.global_media_types:
            shape = make_body_shape(raml, None, node, location)
            raml.put_typedef(shape.location, shape)
            bodies[media_type] = Body(
                id=raml.next_id(),
                media_type=media_type,
                location=location,
                shape=shape,
                value_pos=node.full_position,
            )
    return bodies


# -- responses -----------------------------------------------------------------

#: Spec section Responses: a response key is an HTTP status code. `2xx` and
#: other wildcard spellings are not RAML; nor is a code outside 1xx-5xx.
_STATUS_CODE: Final = re.compile(r'^[1-5][0-9][0-9]$')


def _is_status_code(value: str) -> bool:
    return _STATUS_CODE.match(value) is not None


def _decode_response(raml: Raml, key: Node, value: Node, location: str) -> Response:
    location = raml.location_of(value, location)
    response = Response(
        id=raml.next_id(),
        code=key.value,
        location=location,
        key_pos=key.position,
        value_pos=value.full_position,
    )
    if is_null(value):
        return response
    if value.kind is not NodeKind.MAPPING:
        raise node_error('response must be a mapping', location, value, info={'response': key.value})

    accumulator = Accumulator()
    with raml.target_scope(DomainLocation.RESPONSE):
        for child_key, child_value in pairs(value):
            name = child_key.value
            try:
                if name == FACET_DISPLAY_NAME:
                    response.display_name = make_string_facet(raml, child_key, child_value, location)
                elif name == FACET_DESCRIPTION:
                    response.description = make_string_facet(raml, child_key, child_value, location)
                elif name == FACET_HEADERS:
                    response.headers = make_property_map(raml, child_value, location)
                elif name == FACET_BODY:
                    response.bodies = _decode_bodies(raml, child_value, location, DomainLocation.RESPONSE_BODY)
                elif is_annotation_key(name):
                    _annotation(raml, response.annotations, child_key, child_value, location)
                else:
                    raise node_error('unknown field', location, child_key, info={'field': name})
            except RamlError as err:
                accumulator.add(err)
    accumulator.raise_if_any()
    return response


def _decode_responses(raml: Raml, node: Node, location: str) -> dict[str, Response]:
    if is_null(node):
        return {}
    location = raml.location_of(node, location)
    if node.kind is not NodeKind.MAPPING:
        raise node_error('responses must be a mapping', location, node)
    responses: dict[str, Response] = {}
    accumulator = Accumulator()
    for key, value in pairs(node):
        try:
            if not _is_status_code(key.value):
                raise node_error('status code must be a 3-digit number', location, key, info={'code': key.value})
            if key.value in responses:
                raise node_error('duplicate response', location, key, info={'response': key.value})
            responses[key.value] = _decode_response(raml, key, value, location)
        except RamlError as err:
            accumulator.add(err)
    accumulator.raise_if_any()
    return responses


# -- operations ----------------------------------------------------------------


def _decode_operation_field(  # noqa: PLR0913, PLR0917 - one pass over the method's key vocabulary
    raml: Raml, operation: Operation, request: Request, key: Node, value: Node, location: str
) -> None:
    name = key.value
    if name == FACET_DISPLAY_NAME:
        operation.display_name = make_string_facet(raml, key, value, location)
    elif name == FACET_DESCRIPTION:
        operation.description = make_string_facet(raml, key, value, location)
    elif name == FACET_PROTOCOLS:
        operation.protocols = _protocols(value, location)
    elif name == FACET_HEADERS:
        request.headers = make_property_map(raml, value, location)
    elif name == FACET_QUERY_PARAMETERS:
        request.query_parameters = make_property_map(raml, value, location)
    elif name == FACET_QUERY_STRING:
        request.query_string = make_shape(raml, key, value, location)
        raml.put_typedef(request.query_string.location, request.query_string)
    elif name == FACET_BODY:
        request.bodies = _decode_bodies(raml, value, location, DomainLocation.REQUEST_BODY)
    elif name == FACET_RESPONSES:
        operation.responses = _decode_responses(raml, value, location)
    elif is_annotation_key(name):
        _annotation(raml, operation.annotations, key, value, location)
    else:
        raise node_error('unknown field', location, key, info={'field': name})


def decode_source_operation(raml: Raml, source: SourceOperation) -> Operation:
    """One method's retained tree into an `Operation`."""
    operation = Operation(
        id=raml.next_id(),
        method=source.method,
        location=source.location,
        traits=[*source.rt_traits, *source.traits],
        secured_by=list(source.secured_by),
        key_pos=source.key_pos,
        value_pos=source.value_pos,
    )
    if source.body is None:
        return operation

    location = source.location
    request = Request(id=raml.next_id(), location=location, value_pos=source.value_pos)
    accumulator = Accumulator()
    with raml.active_overlay(source.provenance), _body_scope(raml, source), raml.target_scope(DomainLocation.METHOD):
        for key, value in pairs(source.body):
            try:
                # A facet value that is a provenance boundary root decodes under
                # the scope recorded for it; anything below it is reached
                # through `Raml.scope_for` and `Raml.location_of` instead, which
                # survive the containers the merge synthesised.
                with raml.provenance_scope(value):
                    _decode_operation_field(raml, operation, request, key, value, location)
            except RamlError as err:
                accumulator.add(err)

    if request.query_string is not None and request.query_parameters:
        # Spec section Methods: "Mutually exclusive with queryString."
        accumulator.add(node_error('queryString and queryParameters are mutually exclusive', location, source.body))
    operation.request = request
    accumulator.raise_if_any()
    return operation


# -- endpoints -----------------------------------------------------------------


def _decode_endpoint_field(raml: Raml, endpoint: EndPoint, key: Node, value: Node, location: str) -> None:
    name = key.value
    if name == FACET_DISPLAY_NAME:
        endpoint.display_name = make_string_facet(raml, key, value, location)
    elif name == FACET_DESCRIPTION:
        endpoint.description = make_string_facet(raml, key, value, location)
    elif name == FACET_URI_PARAMETERS:
        endpoint.uri_parameters = make_property_map(raml, value, location)
    elif is_annotation_key(name):
        _annotation(raml, endpoint.annotations, key, value, location)
    else:
        raise node_error('unknown field', location, key, info={'field': name})


def decode_source_endpoint(raml: Raml, source: SourceEndPoint) -> EndPoint:
    """One resource's retained tree into an `EndPoint`, recursing into children.

    URI parameters are decoded here but *not* yet propagated: P6 rewrites each
    map to ancestor-declared parameters first (docs/08 section 8.2), and that
    needs the whole tree, so it runs after this.
    """
    endpoint = EndPoint(
        id=raml.next_id(),
        uri=source.uri,
        full_uri=source.full_uri,
        location=source.location,
        resource_type=source.resource_type,
        traits=[*source.rt_traits, *source.traits],
        secured_by=list(source.secured_by),
        key_pos=source.key_pos,
        value_pos=source.value_pos,
    )
    location = source.location
    accumulator = Accumulator()

    if source.body is not None:
        with (
            raml.active_overlay(source.provenance),
            _body_scope(raml, source),
            raml.target_scope(DomainLocation.RESOURCE),
        ):
            for key, value in pairs(source.body):
                try:
                    with raml.provenance_scope(value):
                        _decode_endpoint_field(raml, endpoint, key, value, location)
                except RamlError as err:
                    accumulator.add(err)

    for method, operation_source in source.operations.items():
        try:
            endpoint.operations[method] = decode_source_operation(raml, operation_source)
        except RamlError as err:
            accumulator.add(err)

    for uri, child in source.endpoints.items():
        try:
            endpoint.endpoints[uri] = decode_source_endpoint(raml, child)
        except RamlError as err:
            accumulator.add(err)

    accumulator.raise_if_any()
    return endpoint

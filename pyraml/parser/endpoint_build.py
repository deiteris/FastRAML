"""P4 and P6 — the endpoint driver.

P4 runs both stages over `APIFragment._raw_endpoints`: stage 1 decodes every
resource into IR, directive resolution merges the templates in, then stage 2
materializes every one of them. **Each loop runs over the whole tree before the
next starts** (docs/08 section 3), because directive resolution grafts
type-bearing subtrees from templates and all of them must exist before the
single decode pass — otherwise a shape a trait contributed never joins the P7
worklist.

P6 then rewrites each endpoint's URI parameter map to ancestor-declared
parameters first, so a nested resource exposes the full set needed to build its
URL, in path order.

See docs/08-templates-and-endpoints.md section 8.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyraml.domains import DomainLocation
from pyraml.errors import Accumulator, ErrorKind, RamlError
from pyraml.parser.resourcetypes import apply_resource_type
from pyraml.parser.source_decode import decode_source_endpoint
from pyraml.parser.source_ir import make_source_endpoint
from pyraml.parser.traits import apply_traits
from pyraml.parser.uritemplates import extract_uri_template_params
from pyraml.registry import ParseCtx
from pyraml.types.base import TYPE_JSON, TYPE_STRING, BaseShape, Property
from pyraml.types.shape import attach_kind

if TYPE_CHECKING:
    from pyraml.parser.endpoints import EndPoint
    from pyraml.parser.source_ir import SourceEndPoint
    from pyraml.registry import Raml

__all__ = ['build_endpoints', 'check_parameter_schemas']


def build_endpoints(raml: Raml) -> None:
    """P4 — decode the API's resources, then P6 — propagate URI parameters."""
    api = raml.entry_point
    if api is None:
        return
    # Only an API declares resources; a Library or a fragment has no `_raw_endpoints`.
    raw = getattr(api, '_raw_endpoints', None)
    if not raw:
        return

    location = api.location
    accumulator = Accumulator()

    # This pass runs after the API's own decode has popped its context, so it
    # re-establishes one. Everything below it — a directive name, a type name in
    # a body — resolves in the API's namespace unless the provenance overlay
    # says otherwise, and nothing here has a namespace of its own.
    raml.push_ctx(ParseCtx(anchor=raml.resolver_at(location), target=DomainLocation.API))
    try:
        # Stage 1, over the whole tree.
        sources = []
        for key, value in raw:
            try:
                sources.append(make_source_endpoint(raml, key, value, location))
            except RamlError as err:
                accumulator.add(err)

        # Between the two stages: resolve `type:` and `is:`. Every subtree a
        # template contributes therefore exists before the single decode pass,
        # so every shape it produces joins the P7 worklist.
        for source in sources:
            _resolve_directives(raml, source, accumulator)

        # Stage 2, over the whole tree.
        for source in sources:
            try:
                _walk(raml, decode_source_endpoint(raml, source), accumulator, inherited={})
            except RamlError as err:
                accumulator.add(err)
    finally:
        raml.pop_ctx()

    accumulator.raise_if_any()


def _resolve_directives(raml: Raml, source: SourceEndPoint, acc: Accumulator) -> None:
    """Apply the resource-type chain, then the traits, then recurse.

    Resource types first: they contribute `is:` entries of their own, which
    `apply_traits` then orders behind the resource's and the method's
    (docs/08 section 5.2).
    """
    if source.resource_type is not None:
        try:
            apply_resource_type(raml, source, source.resource_type, set())
        except RamlError as err:
            acc.add(
                RamlError.wrap(
                    'apply resource type',
                    err,
                    source.location,
                    source.resource_type.value_pos,
                    info={'resourceType': source.resource_type.name},
                )
            )
    try:
        apply_traits(source)
    except RamlError as err:
        acc.add(err)
    for child in source.endpoints.values():
        _resolve_directives(raml, child, acc)


def _walk(raml: Raml, endpoint: EndPoint, acc: Accumulator, *, inherited: dict[str, Property]) -> None:
    if endpoint.full_uri in raml.endpoints:
        # Comparison is on the template text, unexpanded, so `/users/{userId}`
        # and `/users/{username}` coexist while `/users: {/foo:}` and
        # `/users/foo:` collide (docs/08 section 8.1).
        acc.add(
            RamlError.new(
                'duplicate resource URI',
                endpoint.location,
                endpoint.key_pos,
                info={'uri': endpoint.full_uri},
            )
        )
    else:
        raml.endpoints[endpoint.full_uri] = endpoint

    try:
        _resolve_uri_parameters(raml, endpoint, inherited)
    except RamlError as err:
        acc.add(err)

    for child in endpoint.endpoints.values():
        _walk(raml, child, acc, inherited=endpoint.uri_parameters)


def _resolve_uri_parameters(raml: Raml, endpoint: EndPoint, inherited: dict[str, Property]) -> None:
    """docs/08 section 8.2: synthesise, check, and propagate.

    The result is ancestors' parameters first, then this endpoint's, which is
    path order — the order a consumer needs to build the URL.
    """
    declared = endpoint.uri_parameters
    variables = [
        expression.name for expression in extract_uri_template_params(endpoint.uri, endpoint.location, endpoint.key_pos)
    ]

    accumulator = Accumulator()
    for name in declared:
        if name not in variables:
            accumulator.add(
                RamlError.new(
                    'uri parameter is not used',
                    endpoint.location,
                    declared[name].base.key_pos,
                    info={'parameter': name, 'uri': endpoint.uri},
                )
            )
    for prop in declared.values():
        accumulator.add(_check_slash_free(prop))

    own: dict[str, Property] = {}
    for name in variables:
        existing = declared.get(name)
        own[name] = existing if existing is not None else _synthesise(raml, name, endpoint)

    # Ancestors first, then this endpoint's own; a redeclared name wins here.
    endpoint.uri_parameters = {**inherited, **own}
    accumulator.raise_if_any()


def check_parameter_schemas(raml: Raml) -> None:
    """Doc 10 section 6.2's last rule, after P7 has settled every kind.

    Spec section Using XML and JSON Schemas: a type that defines an external
    schema may not be used "in any declaration of query parameters, query
    string, URI parameters, and headers". Base-URI parameters are URI
    parameters and are covered too.

    Not done at the four decoders, as an earlier draft of doc 15 said: a
    parameter may *name* a JSON-schema type rather than declare one inline, and
    the name is not bound until P7. Here every kind is known and the four
    declarations are four fields of a model already built, so the rule has one
    home rather than four — which is also what `_check_slash_free` above does
    for the other parameter-only rule.
    """
    accumulator = Accumulator()
    api = raml.entry_point
    if api is not None:
        _reject_schemas(getattr(api, 'base_uri_parameters', {}), 'baseUriParameters', accumulator)
    for endpoint in raml.endpoints.values():
        _reject_schemas(endpoint.uri_parameters, 'uriParameters', accumulator)
        for operation in endpoint.operations.values():
            request = operation.request
            if request is not None:
                _reject_schemas(request.headers, 'headers', accumulator)
                _reject_schemas(request.query_parameters, 'queryParameters', accumulator)
                if request.query_string is not None and request.query_string.type == TYPE_JSON:
                    accumulator.add(_schema_not_allowed(request.query_string, 'queryString', 'queryString'))
            for response in operation.responses.values():
                _reject_schemas(response.headers, 'headers', accumulator)
    accumulator.raise_if_any()


def _reject_schemas(declared: dict[str, Property], facet: str, accumulator: Accumulator) -> None:
    for name, prop in declared.items():
        if prop.base.type == TYPE_JSON:
            accumulator.add(_schema_not_allowed(prop.base, facet, name))


def _schema_not_allowed(base: BaseShape, facet: str, name: str) -> RamlError:
    return RamlError.new(
        'a JSON schema type is not allowed here',
        base.location,
        base.key_pos,
        kind=ErrorKind.VALIDATING,
        info={'facet': facet, 'parameter': name},
    )


def _synthesise(raml: Raml, name: str, endpoint: EndPoint) -> Property:
    """A template variable with no declaration is a required `string`."""
    base = BaseShape(
        id=raml.next_id(),
        raml=raml,
        location=endpoint.location,
        name=name,
        key_pos=endpoint.key_pos,
        value_pos=endpoint.key_pos,
        anchor=raml.current_ctx().anchor,
    )
    attach_kind(raml, base, TYPE_STRING, [], from_mapping=False)
    raml.put_shape(base)
    # Registered like any other declaration, so P9 and P10 reach it.
    raml.put_typedef(endpoint.location, base)
    return Property(name=name, base=base, required=True)


def _check_slash_free(prop: Property) -> RamlError | None:
    """Spec section Template URIs: a matched value must not contain a slash.

    So a constraint that *names* a value containing one describes something the
    parameter can never match.
    """
    base = prop.base
    candidates: list[tuple[str, object]] = []
    if base.default is not None:
        candidates.append(('default', base.default.raw))
    if base.example is not None and base.example.data is not None:
        candidates.append(('example', base.example.data.raw))
    if base.examples is not None:
        candidates += [
            (f'examples.{name}', example.data.raw)
            for name, example in base.examples.values.items()
            if example.data is not None
        ]
    candidates += [(f'enum[{index}]', member.raw) for index, member in enumerate(base.enum or ())]

    for facet, raw in candidates:
        if isinstance(raw, str) and '/' in raw:
            return RamlError.new(
                'uri parameter value must not contain a slash',
                base.location,
                base.value_pos,
                info={'parameter': prop.name, 'facet': facet},
            )
    return None

"""Read a FastAPI application and build a `Document`.

Two sources, both read straight off the app.

**The models**, through FastAPI's own collection helpers:
`get_fields_from_routes` -> `get_flat_models_from_fields` -> `get_model_name_map`
-> `get_definitions`. That returns one flat, name-mapped dict of JSON Schema,
which `_declaration` turns into `TypeDecl`s. Calling `model_json_schema()` per
model instead does not work: it returns a bare `$ref` for a self-recursive model
and loses its body, and it has no answer for two models of the same name in
different modules.

**The routes**, off each `APIRoute`: `path_format` already uses `{param}` as RAML
does, `get_flat_params` splits path from query from header, `body_field` and
`response_field` carry the payload models, and security comes off the dependency
tree.

Anything the renderer cannot express lands in `Report.dropped` rather than being
omitted in silence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from fastapi._compat import get_definitions
from fastapi.datastructures import DefaultPlaceholder
from fastapi.dependencies.models import (
    _get_oauth_scopes,
    _get_security_scheme,
    _is_security_scheme,
)
from fastapi.dependencies.utils import get_flat_params
from fastapi.openapi.utils import (
    get_fields_from_routes,
    get_flat_models_from_fields,
    get_model_name_map,
)
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from fastapi_raml.document import (
    UNSET,
    Body,
    Document,
    Method,
    Parameters,
    Resource,
    Response,
    SecuredBy,
    SecurityScheme,
    TypeDecl,
    Yaml,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ['Report', 'render']

#: JSON Schema `type` -> the RAML built-in of the same name.
SCALARS: Final[dict[str, str]] = {
    'string': 'string',
    'integer': 'integer',
    'number': 'number',
    'boolean': 'boolean',
}
#: JSON Schema `format` -> the RAML built-in that carries the same meaning.
FORMATS: Final[dict[str, str]] = {
    'date-time': 'datetime',
    'date': 'date-only',
    'time': 'time-only',
    'binary': 'file',
}
#: JSON Schema keyword -> the `TypeDecl` field holding it. Identical names on
#: both sides are still listed, so this table is the whole mapping.
FACETS: Final[dict[str, str]] = {
    'pattern': 'pattern',
    'minLength': 'min_length',
    'maxLength': 'max_length',
    'minimum': 'minimum',
    'maximum': 'maximum',
    'multipleOf': 'multiple_of',
    'minItems': 'min_items',
    'maxItems': 'max_items',
    'uniqueItems': 'unique_items',
    'minProperties': 'min_properties',
    'maxProperties': 'max_properties',
}
#: Keywords a schema node may carry that the renderer consumes. Anything else is
#: reported: `exclusiveMinimum`, `contains`, `not`, a `format` outside FORMATS,
#: and whatever a model config added.
READS: Final[frozenset[str]] = frozenset(FACETS) | {
    'type',
    'title',
    'description',
    'default',
    'format',
    'properties',
    'required',
    'additionalProperties',
    'items',
    'prefixItems',
    'enum',
    'const',
    '$ref',
    'anyOf',
    'oneOf',
    'discriminator',
    'examples',
    'deprecated',
    'readOnly',
    'writeOnly',
}
#: A parameter's constraint attribute -> the `TypeDecl` field it sets. The names
#: come off pydantic's `FieldInfo.metadata`, which is not JSON Schema.
CONSTRAINTS: Final[dict[str, str]] = {
    'pattern': 'pattern',
    'min_length': 'min_length',
    'max_length': 'max_length',
    'ge': 'minimum',
    'le': 'maximum',
    'multiple_of': 'multiple_of',
}
#: An OAuth2 flow as FastAPI names it -> as RAML names it.
GRANTS: Final[dict[str, str]] = {
    'authorizationCode': 'authorization_code',
    'clientCredentials': 'client_credentials',
    'password': 'password',
    'implicit': 'implicit',
}


@dataclass(slots=True)
class Report:
    """A rendered document, and everything the renderer could not express."""

    document: Document
    dropped: list[str] = field(default_factory=list)

    def to_raml(self) -> str:
        return self.document.to_raml()


@dataclass(slots=True)
class _State:
    """What one render carries: its losses, and its tagged-union bookkeeping."""

    dropped: list[str] = field(default_factory=list)
    #: Base types synthesised for tagged unions, by name.
    bases: dict[str, TypeDecl] = field(default_factory=dict)
    #: member name -> (base name, its tag), applied once every type is built.
    rewrites: dict[str, tuple[str, str]] = field(default_factory=dict)

    def drop(self, at: str, what: str) -> None:
        self.dropped.append(f'{at}: {what}')


# --------------------------------------------------------------------------- types


def _expression(js: dict[str, Any]) -> str | None:
    """The RAML type expression for a node, if it has one that fits in a union.

    A union member and an `items:` may be written as an expression, and only as
    one: `string[] | nil` is the spelling for `list[str] | None`, and an inline
    declaration cannot stand there.
    """
    if '$ref' in js:
        ref: str = js['$ref'].rsplit('/', 1)[-1]
        return ref
    kind = js.get('type')
    if kind == 'null':
        return 'nil'
    plain = {'type', 'title', 'description', 'default', 'format'}
    if kind in SCALARS and not (set(js) - plain):
        return FORMATS.get(js.get('format', ''), SCALARS[kind])
    if kind == 'array' and not (set(js) - plain - {'items'}):
        inner = _expression(js.get('items', {}))
        if inner is not None and '|' not in inner:
            return f'{inner}[]'
    return None


def _union(parts: Iterable[dict[str, Any]], at: str, st: _State) -> TypeDecl:
    members = [_expression(part) for part in parts]
    if any(member is None for member in members):
        st.drop(at, 'union member is an inline declaration, not a named type')
        return TypeDecl(type='any')
    return TypeDecl(type=' | '.join(dict.fromkeys(member for member in members if member)))


def _tagged_union(js: dict[str, Any], at: str, st: _State) -> TypeDecl:
    """A pydantic tagged union -> RAML's discriminator, with dispatch.

    RAML splits what pydantic states in one place. `discriminator` names the tag
    property and lives on a *base* type; `discriminatorValue` identifies each
    concrete subtype. pydantic has no base, so one is synthesised here and the
    members are rewritten to inherit it in `_types`.

    The **use site** is the union of the members, not the base. `discriminator`
    MUST NOT appear on a union type (spec l.833) and dispatch is only a MAY for a
    processor (l.762), so the base carries the declaration and the union is what
    selects.
    """
    prop = js['discriminator']['propertyName']
    base = js.get('title') or at.replace('.', '_').replace('[]', 'Item')
    st.bases[base] = TypeDecl(type='object', discriminator=prop, properties={prop: TypeDecl(type='string')})
    for tag, ref in js['discriminator']['mapping'].items():
        st.rewrites[ref.rsplit('/', 1)[-1]] = (base, tag)
    members = [ref.rsplit('/', 1)[-1] for ref in js['discriminator']['mapping'].values()]
    return TypeDecl(type=' | '.join(dict.fromkeys(members)))


def _declaration(js: dict[str, Any], at: str, st: _State) -> TypeDecl:
    """One JSON Schema node -> one RAML type declaration.

    Three shapes in order: a node that fits in a type expression, a union, and
    everything else -- a kind plus the facets any kind may carry.
    """
    plain = _expression(js)
    if plain is not None and 'description' not in js:
        return TypeDecl(type=plain)
    if 'oneOf' in js and 'discriminator' in js:
        return _tagged_union(js, at, st)
    for key in ('anyOf', 'oneOf'):
        if key in js:
            return _union(js[key], at, st)

    unread = set(js) - READS
    if unread:
        st.drop(at, 'schema keyword(s) with no RAML facet: ' + ', '.join(sorted(unread)))
    decl = _kind(js, at, st)
    _facets(decl, js)
    return decl


def _kind(js: dict[str, Any], at: str, st: _State) -> TypeDecl:
    """The RAML kind a schema node declares, and whatever that kind contains."""
    kind = js.get('type')
    if kind == 'object':
        return _object(js, at, st)
    if kind == 'array':
        if 'prefixItems' in js:  # a tuple; RAML arrays are homogeneous
            st.drop(at, 'tuple rendered as an unconstrained array')
        return TypeDecl(type='array', items=_declaration(js.get('items', {}), f'{at}[]', st))
    if kind == 'null':
        return TypeDecl(type='nil')
    if kind in SCALARS:
        return TypeDecl(type=FORMATS.get(js.get('format', ''), SCALARS[kind]))
    # No `type`, which `_facets` may still settle from `const` or `enum`.
    return TypeDecl()


def _object(js: dict[str, Any], at: str, st: _State) -> TypeDecl:
    decl = TypeDecl(type='object')
    required = set(js.get('required', []))
    for name, sub in js.get('properties', {}).items():
        prop = _declaration(sub, f'{at}.{name}', st)
        if name not in required:
            prop.required = False
        if 'description' in sub:
            prop.description = sub['description']
        if 'default' in sub:
            prop.default = sub['default']
        decl.properties[name] = prop
    extra = js.get('additionalProperties')
    if isinstance(extra, dict):  # dict[str, X] and RootModel[dict[str, X]]
        decl.properties['//'] = _declaration(extra, f'{at}.//', st)
    elif extra is False:
        decl.additional_properties = False
    return decl


def _facets(decl: TypeDecl, js: dict[str, Any]) -> None:
    """The constraints, enum and description any kind may carry.

    `const` and `enum` may be the only thing a node says, so they settle `type`
    where `_kind` found none -- `{"const": "cat"}` is a string in RAML.
    """
    if 'const' in js:
        decl.type = decl.type or 'string'
        decl.enum = [js['const']]
    elif 'enum' in js:
        decl.type = decl.type or 'string'
        decl.enum = list(js['enum'])
    for keyword, attribute in FACETS.items():
        if keyword in js:
            setattr(decl, attribute, js[keyword])
    if 'description' in js:
        decl.description = js['description']
    if decl.type is None:
        decl.type = 'any'


def _types(routes: list[APIRoute], st: _State) -> tuple[dict[str, TypeDecl], dict[Any, str]]:
    fields = get_fields_from_routes(routes)
    flat = get_flat_models_from_fields(fields, known_models=set())
    names = get_model_name_map(flat)
    _, definitions = get_definitions(fields=fields, model_name_map=names, separate_input_output_schemas=True)
    types = {name: _declaration(js, name, st) for name, js in definitions.items()}

    for member, (base, tag) in st.rewrites.items():
        decl = types.get(member)
        if decl is None:
            continue
        decl.type = base
        decl.discriminator_value = tag
        # `Literal['cat'] = 'cat'` carries a default, so the tag arrives optional.
        # In a tagged union it is not: the default applies only once a member has
        # been chosen, and choosing one is what the tag is for. Left optional, the
        # RAML accepts a payload pydantic cannot dispatch.
        prop = decl.properties.get(st.bases[base].discriminator or '')
        if prop is not None:
            prop.required = None
            prop.default = UNSET
    types.update(st.bases)
    return types, names


# ---------------------------------------------------------------------- endpoints


def _parameter(param: Any, at: str, st: _State) -> TypeDecl:
    """A path, query or header parameter, through the same path as a model."""
    info = param.field_info
    try:
        js = TypeAdapter(info.annotation).json_schema()
    except Exception:  # noqa: BLE001 - an annotation pydantic will not schematise alone
        st.drop(at, f'parameter type {info.annotation!r} rendered as string')
        js = {'type': 'string'}
    decl = _declaration(js, at, st)
    for meta in info.metadata:
        for attribute, target in CONSTRAINTS.items():
            value = getattr(meta, attribute, None)
            if value is not None:
                setattr(decl, target, value)
    if not info.is_required():
        decl.required = False
        if info.default is not None and info.default is not Ellipsis:
            decl.default = info.default
    if info.description:
        decl.description = info.description
    if info.examples:
        decl.examples = {f'e{index}': value for index, value in enumerate(info.examples)}
    return decl


def _security(routes: list[APIRoute], st: _State) -> dict[str, SecurityScheme]:
    """`securitySchemes:`, harvested off each route's dependency tree."""
    schemes: dict[str, SecurityScheme] = {}

    def visit(dependant: Any) -> None:
        if _is_security_scheme(dependant=dependant):
            scheme = _get_security_scheme(dependant=dependant)
            model = scheme.model.model_dump(exclude_none=True)
            kind = getattr(model['type_'], 'value', model['type_'])
            built = _scheme(kind, model, scheme.scheme_name, st)
            if built is not None:
                schemes[scheme.scheme_name] = built
        for sub in dependant.dependencies:
            visit(sub)

    for route in routes:
        visit(route.dependant)
    return schemes


def _scheme(kind: str, model: dict[str, Any], name: str, st: _State) -> SecurityScheme | None:
    if kind == 'oauth2':
        flow, config = next(iter(model['flows'].items()))
        settings: dict[str, Yaml] = {'authorizationGrants': [GRANTS.get(flow, flow)]}
        if config.get('authorizationUrl'):
            settings['authorizationUri'] = config['authorizationUrl']
        if config.get('tokenUrl'):
            settings['accessTokenUri'] = config['tokenUrl']
        if config.get('scopes'):
            settings['scopes'] = sorted(config['scopes'])
        return SecurityScheme(type='OAuth 2.0', settings=settings)
    if kind == 'http' and model.get('scheme') == 'basic':
        return SecurityScheme(type='Basic Authentication')
    if kind == 'http' and model.get('scheme') == 'digest':
        return SecurityScheme(type='Digest Authentication')
    if kind == 'apiKey':
        where = {'header': 'headers', 'query': 'queryParameters'}.get(model.get('in_', ''))
        if where is None:
            st.drop(name, f'apiKey in {model.get("in_")!r} has no RAML form')
            return None
        return SecurityScheme(type='Pass Through', described_by={where: {model['name']: {'type': 'string'}}})
    st.drop(name, f'security scheme kind {kind!r} has no RAML form')
    return None


def _secured_by(dependant: Any, out: list[SecuredBy]) -> None:
    if _is_security_scheme(dependant=dependant):
        out.append(
            SecuredBy(
                scheme=_get_security_scheme(dependant=dependant).scheme_name,
                scopes=_get_oauth_scopes(dependant=dependant),
            )
        )
    for sub in dependant.dependencies:
        _secured_by(sub, out)


def _method(route: APIRoute, verb: str, names: dict[Any, str], st: _State) -> tuple[Method, Parameters]:
    at = f'{verb} {route.path_format}'
    method = Method(display_name=route.summary or None, description=route.description or None)

    uri: Parameters = {}
    buckets: dict[str, Parameters] = {'Path': uri, 'Query': method.query_parameters, 'Header': method.headers}
    for param in get_flat_params(route.dependant):
        bucket = buckets.get(type(param.field_info).__name__)
        if bucket is None:
            st.drop(at, f'{type(param.field_info).__name__} parameter {param.alias!r} has no RAML form')
            continue
        bucket[param.alias] = _parameter(param, f'{at}.{param.alias}', st)

    if route.body_field is not None:
        info = route.body_field.field_info
        media = getattr(info, 'media_type', None) or 'application/json'
        method.body = Body({media: TypeDecl(type=names.get(info.annotation, 'any'))})

    if route.response_field is not None:
        # `response_class` is a DefaultPlaceholder unless the route set one, and
        # the real class is behind `.value` -- the unwrap FastAPI itself does.
        response_class = route.response_class
        if isinstance(response_class, DefaultPlaceholder):
            response_class = response_class.value
        media = response_class.media_type or 'application/json'
        name = names.get(route.response_field.field_info.annotation, 'any')
        method.responses[route.status_code or 200] = Response(body=Body({media: TypeDecl(type=name)}))
    for key, spec in (route.responses or {}).items():
        # FastAPI accepts a wildcard range as a string key -- `"4XX"` -- and RAML
        # has no spelling for one: a response key is a status code.
        if not (isinstance(key, int) or (isinstance(key, str) and key.isdigit())):
            st.drop(at, f'response key {key!r} is a range, and RAML keys responses by status code')
            continue
        response = method.responses.setdefault(int(key), Response())
        if spec.get('description'):
            response.description = spec['description']
        if spec.get('model') is not None:
            model_name = names.get(spec['model'], 'any')
            response.body = Body({'application/json': TypeDecl(type=model_name)})

    _secured_by(route.dependant, method.secured_by)
    if route.callbacks:
        st.drop(at, 'callbacks have no RAML form')
    return method, uri


def render(app: Any) -> Report:
    """Render `app` as a RAML 1.0 document."""
    st = _State()
    document = Document(title=app.title, version=app.version or None, description=app.description or None)
    if app.servers:
        document.base_uri = app.servers[0]['url']
        for name, spec in (app.servers[0].get('variables') or {}).items():
            document.base_uri_parameters[name] = TypeDecl(type='string', default=spec.get('default'))
        if len(app.servers) > 1:
            st.drop('servers', f'{len(app.servers) - 1} extra server(s); RAML has one baseUri')

    routes = [route for route in app.routes if isinstance(route, APIRoute) and route.include_in_schema]
    document.types, names = _types(routes, st)
    document.security_schemes = _security(routes, st)

    for route in routes:
        resource: Resource = document.root.at(route.path_format)
        for verb in sorted(route.methods or ()):
            method, uri = _method(route, verb, names, st)
            resource.methods[verb.lower()] = method
            resource.uri_parameters.update(uri)
    return Report(document=document, dropped=st.dropped)

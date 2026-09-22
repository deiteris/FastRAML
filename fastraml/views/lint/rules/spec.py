"""What RAML 1.0 recommends, or leaves undefined, and the parser accepts.

Each rule here follows from a clause of the RAML 1.0 specification that a
conforming processor may not reject: a SHOULD, a reserved name with nothing to
supply it, a construct whose meaning the spec says it does not define, or a
node that silently overrides another. The MUSTs are the parser's. Every rule
cites its section in `references`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, Final
from urllib.parse import urlsplit

from fastraml.parser.uritemplates import extract_uri_template_params
from fastraml.positions import UNKNOWN
from fastraml.types.complex_ import ArrayShape, ObjectShape, UnionShape
from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.parser.endpoints import EndPoint, Operation
    from fastraml.parser.fragments import APIFragment
    from fastraml.parser.security import SecuritySchemeDefinition
    from fastraml.types.base import BaseShape, Parameter
    from fastraml.views.lint.engine import Context

__all__ = [
    'BaseUriProtocol',
    'EmptyPathSegment',
    'NonScalarParameter',
    'NonStandardMethod',
    'UndefinedVersion',
    'UndescribedSecurityScheme',
    'UnnestedResource',
]

#: RAML 1.0 § Methods: the methods a resource may declare. fastRAML also
#: accepts `trace` and `connect` as an extension (docs/01 § 3).
_RAML_METHODS: Final = frozenset({'get', 'patch', 'put', 'post', 'delete', 'head', 'options'})
_WEB_SCHEMES: Final = frozenset({'http', 'https'})
#: A segment that is one simple expansion, `{name}`; the parser has validated the name.
_PARAMETER_SEGMENT: Final = re.compile(r'\{([^{}+#][^{}]*)\}')


def _prefix(endpoint: EndPoint) -> str:
    """The ancestors' part of `endpoint.full_uri`."""
    return endpoint.full_uri[: len(endpoint.full_uri) - len(endpoint.uri)]


def _optional_segment_parameters(endpoint: EndPoint) -> list[str]:
    """The optional parameters that each fill one of this resource's own segments, `/{name}/`."""
    matches = (_PARAMETER_SEGMENT.fullmatch(segment) for segment in endpoint.uri.split('/')[1:])
    parameters = (endpoint.uri_parameters.get(match.group(1)) for match in matches if match is not None)
    return [parameter.name for parameter in parameters if parameter is not None and not parameter.required]


def _names_version(uri: str) -> bool:
    """Whether a template the parser has already validated uses the reserved `version`."""
    return any(expression.name == 'version' for expression in extract_uri_template_params(uri, '', UNKNOWN))


class EmptyPathSegment:
    meta: ClassVar = RuleMeta(
        'empty-path-segment',
        Category.SPEC,
        'resource paths should not admit an empty segment',
        (
            'Adjacent slashes enclosing no characters usually make no sense in a URI, so a URI parameter that '
            'fills a whole path segment should be required: an optional `/{id}/` lets the path become `//`. '
            'Make it optional only where it sits beside other text, as in `/people/~{fields}`. A path written '
            'with `//`, or a nested resource under a parent ending in `/`, has the empty segment already.'
        ),
        Severity.WARNING,
        references=('RAML 1.0 § Template URIs and URI Parameters',),
        good='#%RAML 1.0\ntitle: t\n/items/{id}:\n  get:\n',
        bad='#%RAML 1.0\ntitle: t\n/items/{id}:\n  uriParameters:\n    id?: string\n  get:\n',
    )

    def endpoint(self, ctx: Context, iri: str, endpoint: EndPoint) -> Iterable[Finding]:
        joined = _prefix(endpoint)[-1:] + endpoint.uri
        findings = []
        if '//' in joined.rstrip('/'):
            findings.append(
                ctx.on(self.meta, 'resource path contains an empty segment', endpoint, iri=iri, path=endpoint.full_uri)
            )
        findings += [
            ctx.on(
                self.meta,
                'optional URI parameter fills a whole path segment',
                endpoint,
                iri=iri,
                path=endpoint.full_uri,
                parameter=name,
            )
            for name in _optional_segment_parameters(endpoint)
        ]
        return findings


class UnnestedResource:
    meta: ClassVar = RuleMeta(
        'unnested-resource',
        Category.SPEC,
        'a resource under another resource should be nested in it',
        (
            'A resource key may hold several path segments, as `/bom/items` does, but when a leading part of '
            'it is itself a resource the definition should nest the rest under that resource. Nesting is what '
            'lets the child inherit URI parameters and what tells a reader the two are related.'
        ),
        Severity.WARNING,
        references=('RAML 1.0 § Resources and Nested Resources',),
        good='#%RAML 1.0\ntitle: t\n/bom:\n  get:\n  /items:\n    get:\n',
        bad='#%RAML 1.0\ntitle: t\n/bom:\n  get:\n/bom/items:\n  get:\n',
    )

    def endpoint(self, ctx: Context, iri: str, endpoint: EndPoint) -> Iterable[Finding]:
        segments = endpoint.uri.split('/')[1:]
        prefix = _prefix(endpoint)
        for count in range(len(segments) - 1, 0, -1):
            candidate = prefix + '/' + '/'.join(segments[:count])
            if candidate in ctx.raml.endpoints:
                return (
                    ctx.on(
                        self.meta,
                        'resource is written beside a resource it extends',
                        endpoint,
                        iri=iri,
                        path=endpoint.full_uri,
                        resource=candidate,
                    ),
                )
        return ()


class BaseUriProtocol:
    meta: ClassVar = RuleMeta(
        'base-uri-protocol',
        Category.SPEC,
        '`protocols` should include the baseUri scheme',
        (
            'An explicit `protocols` node overrides the protocol in `baseUri`. When it leaves that protocol '
            'out, the base URI the contract publishes names an address the API says it does not serve. '
            'Change the scheme of `baseUri` or add it to `protocols`.'
        ),
        Severity.WARNING,
        references=('RAML 1.0 § Protocols',),
        good='#%RAML 1.0\ntitle: t\nbaseUri: https://api.example.com\nprotocols: [HTTPS]\n',
        bad='#%RAML 1.0\ntitle: t\nbaseUri: https://api.example.com\nprotocols: [HTTP]\n',
    )

    def api(self, ctx: Context, iri: str, api: APIFragment) -> Iterable[Finding]:
        if api.base_uri is None or not api.protocols:
            return ()
        scheme = urlsplit(api.base_uri.value).scheme.casefold()
        declared = {protocol.value.casefold() for protocol in api.protocols}
        if scheme not in _WEB_SCHEMES or scheme in declared:
            return ()
        first = api.protocols[0]
        return (
            ctx.on(
                self.meta,
                'protocols leaves out the baseUri scheme',
                first,
                iri=iri,
                position=first.value_pos,
                scheme=scheme.upper(),
                protocols=','.join(protocol.value.upper() for protocol in api.protocols),
            ),
        )


class UndefinedVersion:
    meta: ClassVar = RuleMeta(
        'undefined-version',
        Category.SPEC,
        '`{version}` should have a root `version` to take its value from',
        (
            '`version` is a reserved URI parameter whose value is the root-level `version` node. A base URI or '
            'resource that writes `{version}` in a document with no `version` has nothing to substitute, and '
            'every client has to guess the value.'
        ),
        Severity.WARNING,
        references=('RAML 1.0 § Base URI and Base URI Parameters', 'RAML 1.0 § Template URIs and URI Parameters'),
        good='#%RAML 1.0\ntitle: t\nversion: v1\nbaseUri: https://api.example.com/{version}\n',
        bad='#%RAML 1.0\ntitle: t\nbaseUri: https://api.example.com/{version}\n',
    )

    def api(self, ctx: Context, iri: str, api: APIFragment) -> Iterable[Finding]:
        if api.version is not None or api.base_uri is None or not _names_version(api.base_uri.value):
            return ()
        return (ctx.on(self.meta, 'version is used but not defined', api.base_uri, iri=iri, uri=api.base_uri.value),)

    def endpoint(self, ctx: Context, iri: str, endpoint: EndPoint) -> Iterable[Finding]:
        if getattr(ctx.raml.entry_point, 'version', None) is not None or not _names_version(endpoint.uri):
            return ()
        return (ctx.on(self.meta, 'version is used but not defined', endpoint, iri=iri, uri=endpoint.full_uri),)


class UndescribedSecurityScheme:
    meta: ClassVar = RuleMeta(
        'undescribed-security-scheme',
        Category.SPEC,
        'security schemes should declare describedBy',
        (
            'Even for a standard scheme, API designers should describe the headers, query parameters and '
            'responses the scheme adds, because that is what completes the documentation of a secured '
            'request. For `Pass Through` and `x-` schemes, whose settings define nothing, `describedBy` is the '
            'only statement of what a client must send.'
        ),
        Severity.WARNING,
        references=('RAML 1.0 § Security Scheme Declaration',),
        good=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  key:\n    type: Pass Through\n    describedBy:\n'
            '      headers:\n        X-Api-Key: string\n'
        ),
        bad='#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  key:\n    type: Pass Through\n',
    )

    def security_scheme(self, ctx: Context, iri: str, definition: SecuritySchemeDefinition) -> Iterable[Finding]:
        resolved = definition.resolved()
        if resolved.described_by is not None:
            return ()
        return (
            ctx.on(
                self.meta,
                'security scheme has no describedBy',
                definition,
                iri=iri,
                scheme=definition.name,
                type=resolved.type,
            ),
        )


def _non_scalar(base: BaseShape) -> bool:
    shape = base.shape
    if isinstance(shape, (ObjectShape, ArrayShape)):
        return True
    return isinstance(shape, UnionShape) and any(_non_scalar(member) for member in shape.any_of or ())


def _undefined_format(parameter: Parameter) -> str | None:
    """Why RAML defines no serialization for this header or query parameter, if it does not."""
    shape = parameter.base.shape
    if isinstance(shape, ObjectShape):
        return 'object'
    if isinstance(shape, UnionShape) and _non_scalar(parameter.base):
        return 'union of non-scalar types'
    if isinstance(shape, ArrayShape) and shape.items is not None:
        items = shape.items.shape
        if isinstance(items, ObjectShape):
            return 'array of objects'
        if isinstance(items, UnionShape) and _non_scalar(shape.items):
            return 'array of non-scalar unions'
        if isinstance(items, ArrayShape) and parameter.binding == 'header':
            return 'array of arrays'
    return None


class NonScalarParameter:
    meta: ClassVar = RuleMeta(
        'non-scalar-parameter',
        Category.SPEC,
        'headers and query parameters should have a serialization RAML defines',
        (
            'RAML does not define validation for a header or query parameter typed as an object, a union of '
            'non-scalar types, or an array of either; for a header, an array of arrays too. A processor may '
            'read the value as JSON or do something else, so client and server can disagree about what is on '
            'the wire. Use scalars, arrays of scalars, or a body.'
        ),
        Severity.WARNING,
        references=('RAML 1.0 § Headers', 'RAML 1.0 § Query Parameters in a Query String'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    queryParameters:\n      tags: string[]\n',
        bad=(
            '#%RAML 1.0\ntitle: t\n/a:\n  get:\n    queryParameters:\n      filter:\n'
            '        properties:\n          tag: string\n'
        ),
    )

    def parameter(self, ctx: Context, iri: str, parameter: Parameter) -> Iterable[Finding]:
        if parameter.binding not in ('header', 'query'):
            return ()
        reason = _undefined_format(parameter)
        if reason is None:
            return ()
        return (
            ctx.on(
                self.meta,
                'parameter type has no serialization RAML defines',
                parameter.base,
                iri=iri,
                position=parameter.key_pos,
                parameter=parameter.name,
                binding=parameter.binding,
                reason=reason,
            ),
        )


class NonStandardMethod:
    meta: ClassVar = RuleMeta(
        'non-standard-method',
        Category.SPEC,
        'resources should declare only the methods RAML 1.0 defines',
        (
            'RAML 1.0 defines get, patch, put, post, delete, head and options. fastRAML also accepts trace and '
            'connect, but other RAML processors reject a document that declares them.'
        ),
        Severity.WARNING,
        references=('RAML 1.0 § Methods',),
        good='#%RAML 1.0\ntitle: t\n/a:\n  options:\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  trace:\n',
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        if operation.method in _RAML_METHODS:
            return ()
        return (ctx.on(self.meta, 'method is not a RAML 1.0 method', operation, iri=iri, method=operation.method),)

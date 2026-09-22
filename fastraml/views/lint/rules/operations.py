"""Rules about effective HTTP operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final

from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.parser.endpoints import Operation
    from fastraml.views.lint.engine import Context

__all__ = ['MeaninglessRequestBody', 'UnsecuredOperation']

#: Methods whose request content HTTP gives no meaning, with the clause that says so.
_BODILESS_METHODS: Final = {
    'get': 'RFC 9110 § 9.3.1',
    'head': 'RFC 9110 § 9.3.2',
    'delete': 'RFC 9110 § 9.3.5',
    'trace': 'RFC 9110 § 9.3.8',
}


class MeaninglessRequestBody:
    meta: ClassVar = RuleMeta(
        id='meaningless-request-body',
        category=Category.SPEC,
        summary='a GET, HEAD, DELETE or TRACE operation that declares a request body',
        rationale=(
            'RAML permits a body on any method, but content in a GET, HEAD or DELETE request "has no generally '
            'defined semantics, cannot alter the meaning or target of the request", and implementations may '
            'reject it as a request-smuggling risk; a client MUST NOT send content in a TRACE request. The '
            'contract therefore cannot tell a consumer that sending the body will work. Formerly '
            '`get-with-body`, which covered GET only.'
        ),
        severity=Severity.WARNING,
        references=('RFC 9110 § 9.3.1', 'RFC 9110 § 9.3.2', 'RFC 9110 § 9.3.5', 'RFC 9110 § 9.3.8'),
        good='#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json:\n        type: string\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  delete:\n    body:\n      application/json:\n        type: string\n',
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        clause = _BODILESS_METHODS.get(operation.method)
        if clause is None or operation.request is None or not operation.request.bodies:
            return ()
        return (
            ctx.on(
                self.meta,
                'operation declares a request body HTTP gives no meaning',
                operation,
                iri=iri,
                method=operation.method,
                clause=clause,
            ),
        )


class UnsecuredOperation:
    meta: ClassVar = RuleMeta(
        id='unsecured-operation',
        category=Category.SECURITY,
        summary='an operation reachable without a security scheme',
        rationale=(
            'After RAML security inheritance and an explicit '
            '`securedBy: [null]` are applied, an operation with no scheme accepts requests without the '
            'authentication described by the API. OWASP counts a service that others can reach without '
            'authentication as vulnerable.'
        ),
        severity=Severity.WARNING,
        references=('OWASP API2:2023', 'CWE-306'),
        good=(
            '#%RAML 1.0\ntitle: t\nsecuritySchemes:\n  basic:\n    type: Basic Authentication\n'
            'securedBy: [basic]\n/a:\n  get:\n'
        ),
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n',
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        if operation.secured_by and all(not scheme.is_null for scheme in operation.secured_by):
            return ()
        return (ctx.on(self.meta, 'operation has no security scheme', operation, iri=iri, method=operation.method),)

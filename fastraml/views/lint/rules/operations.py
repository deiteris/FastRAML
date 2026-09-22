"""Rules about effective HTTP operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from fastraml.views.lint.engine import Category, Finding, RuleMeta, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastraml.parser.endpoints import Operation
    from fastraml.views.lint.engine import Context

__all__ = ['GetWithBody', 'UnsecuredOperation']


class GetWithBody:
    meta: ClassVar = RuleMeta(
        id='get-with-body',
        category=Category.SPEC,
        summary='a GET operation that declares a request body',
        rationale=(
            'RAML permits a body on GET, but HTTP gives it no generally defined semantics and implementations '
            'may reject or ignore it. The contract therefore cannot tell a consumer that sending it will work.'
        ),
        severity=Severity.WARNING,
        good='#%RAML 1.0\ntitle: t\n/a:\n  post:\n    body:\n      application/json:\n        type: string\n',
        bad='#%RAML 1.0\ntitle: t\n/a:\n  get:\n    body:\n      application/json:\n        type: string\n',
    )

    def operation(self, ctx: Context, iri: str, operation: Operation) -> Iterable[Finding]:
        if operation.method != 'get' or operation.request is None or not operation.request.bodies:
            return ()
        return (
            ctx.on(self.meta, 'GET operation declares a request body', operation, iri=iri, method=operation.method),
        )


class UnsecuredOperation:
    meta: ClassVar = RuleMeta(
        id='unsecured-operation',
        category=Category.SECURITY,
        summary='an operation reachable without a security scheme',
        rationale=(
            'OWASP API2:2023. After RAML security inheritance and an explicit `securedBy: [null]` are applied, '
            'an operation with no scheme accepts requests without the authentication described by the API.'
        ),
        severity=Severity.WARNING,
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

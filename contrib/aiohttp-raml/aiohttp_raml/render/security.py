"""`securitySchemes:`, from what `security.setup` registered, and each handler's `securedBy`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from raml_document import SecuredBy, SecurityScheme

from aiohttp_raml.security import AUTH_SCHEMES

if TYPE_CHECKING:
    from raml_document.from_pydantic import Walk

    from aiohttp_raml.decorator import Described

__all__ = ['schemes', 'secured_by']


def schemes(app: Any) -> dict[str, SecurityScheme]:
    """`securitySchemes:`, from what `security.setup` registered.

    Every registered scheme is declared, used or not: that is what the app says
    about itself, and RAML permits a declaration nothing refers to.
    """
    registry = app.get(AUTH_SCHEMES) or {}
    return {name: scheme.declare() for name, scheme in registry.items()}


def secured_by(entry: Described, declared: dict[str, SecurityScheme], at: str, walk: Walk) -> list[SecuredBy]:
    out: list[SecuredBy] = []
    for name, scopes in entry.secured_by:
        scheme = declared.get(name)
        if scheme is None:
            walk.drop(at, f'securedBy {name!r} is not written: no scheme of that name is registered')
            continue
        if scopes and scheme.type != 'OAuth 2.0':
            walk.drop(at, f'{name!r} carries scopes {sorted(scopes)}, and RAML scopes belong to OAuth 2.0')
            out.append(SecuredBy(scheme=name))
            continue
        out.append(SecuredBy(scheme=name, scopes=list(scopes)))
    return out

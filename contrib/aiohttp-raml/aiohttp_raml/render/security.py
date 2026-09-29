"""`securitySchemes:`, from what `security.setup` registered, and each handler's `securedBy`."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
from typing import TYPE_CHECKING

from raml_document import SecuredBy, SecurityScheme

from aiohttp_raml.security import AUTH_SCHEMES

if TYPE_CHECKING:
    from raml_document.from_pydantic import Walk

    from aiohttp_raml.decorator import Described
    from aiohttp_raml.render.routes import Chain
    from aiohttp_raml.security import SecurityScheme as Registered

__all__ = ['Security']


@dataclass(slots=True)
class Security:
    """The schemes registered across an app and its sub-applications, each declared once.

    `declared` is `securitySchemes:`. Every scheme an app registers is
    declared, used or not: that is what the app says about itself, and RAML
    permits a declaration nothing refers to.
    """

    walk: Walk
    declared: dict[str, SecurityScheme] = field(default_factory=dict)
    #: Each scheme met, and the name it was declared under. A list searched by
    #: identity: a scheme is a dataclass, and two configured alike are still
    #: two registrations.
    _met: list[tuple[Registered, str]] = field(default_factory=list)

    def names(self, chain: Chain) -> dict[str, str]:
        """For a handler in the innermost app of `chain`: each name `@secured` can use -> its declared name.

        A handler looks a scheme up through `request.config_dict`, so it sees
        every app's registry on the way down, the innermost winning.
        """
        visible: dict[str, Registered] = {}
        for app in chain:
            visible.update(app.get(AUTH_SCHEMES) or {})
        return {name: self._declare(name, scheme) for name, scheme in visible.items()}

    def _declare(self, name: str, scheme: Registered) -> str:
        """The name `scheme` is declared under, declaring it on first sight.

        Two sub-applications may register different schemes under one name.
        The second is renamed and reported, rather than described as the first.
        """
        for met, declared in self._met:
            if met is scheme:
                return declared
        declared = name
        if declared in self.declared:
            declared = next(f'{name}_{number}' for number in count(2) if f'{name}_{number}' not in self.declared)
            self.walk.drop(
                'securitySchemes', f'two schemes are registered as {name!r}; the second is declared as {declared}'
            )
        self.declared[declared] = scheme.declare()
        self._met.append((scheme, declared))
        return declared

    def secured_by(self, entry: Described, names: dict[str, str], at: str) -> list[SecuredBy]:
        """`securedBy:` for one handler, naming each scheme as `names` says it was declared.

        A scope is written only where RAML accepts it: on an OAuth 2.0 scheme
        whose `scopes` lists it. The app declares its schemes itself, so a scope
        they do not list is its own inconsistency -- reported, and left out
        rather than failing the whole document.
        """
        out: list[SecuredBy] = []
        for name, scopes in entry.secured_by:
            declared = names.get(name)
            if declared is None:
                self.walk.drop(at, f'securedBy {name!r} is not written: no scheme of that name is registered')
                continue
            scheme = self.declared[declared]
            if scopes and scheme.type != 'OAuth 2.0':
                self.walk.drop(at, f'{name!r} carries scopes {sorted(scopes)}, and RAML scopes belong to OAuth 2.0')
                out.append(SecuredBy(scheme=declared))
                continue
            listed = scheme.settings.get('scopes')
            listed = listed if isinstance(listed, list) else []
            unlisted = [scope for scope in scopes if scope not in listed]
            if unlisted:
                self.walk.drop(at, f'{name!r} is asked for scopes {unlisted} it does not declare; not written')
            out.append(SecuredBy(scheme=declared, scopes=[scope for scope in scopes if scope not in unlisted]))
        return out

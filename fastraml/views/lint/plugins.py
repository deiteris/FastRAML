"""Entry-point discovery for third-party lint rules."""

from __future__ import annotations

from collections.abc import Sequence
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastraml.views.lint.engine import Registry

__all__ = ['discover_plugins']


def _rules_from(exported: Any) -> Sequence[Any]:
    rules: object = exported() if callable(exported) else exported
    if isinstance(rules, (str, bytes)) or not isinstance(rules, Sequence):
        raise TypeError('must export a sequence of rules')
    return rules


def discover_plugins(registry: Registry) -> set[str]:
    """Discover every provider, registering its rules without enabling them."""
    found: set[str] = set()
    for entry in entry_points(group='fastraml.lint_rules'):
        try:
            exported: Any = entry.load()
            rules = _rules_from(exported)
            distribution = entry.dist.name if entry.dist is not None else entry.name
            for rule in rules:
                registry.add(rule, source=distribution, plugin=entry.name)
        except Exception as err:
            raise ValueError(f'cannot load lint plugin {entry.name!r}: {err}') from err
        found.add(entry.name)
    return found

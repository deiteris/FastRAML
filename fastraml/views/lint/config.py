"""Lint configuration decoding (docs/18-linting.md § 3).

**Structure is validated by a RAML type, not by hand.** `config.raml` beside
this module declares the shape — which fields exist, what each holds, which
severities spell a severity, that no unknown key is tolerated — and
`LintConfig.validate` enforces it. This module keeps only what a type cannot
express: whether a rule, ruleset, plugin or category *exists* depends on what is
registered at run time, and whether a `match:` string compiles depends on the
regex engine.

The schema doubles as something an editor can check a configuration against.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from fastraml.config import schema_type
from fastraml.views.lint.engine import Config, Registry, RuleSetting, parse_severity

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastraml.types.base import BaseShape

__all__ = ['config_shape', 'parse_config']

#: The RAML library declaring the configuration's shape. Shipped inside the
#: package, so the schema an editor is given is the one this build enforces.
SCHEMA = Path(__file__).parent / 'config.raml'

#: The declaration in it that a `lint.yaml` must conform to.
ROOT = 'LintConfig'


@lru_cache(maxsize=1)
def config_shape() -> BaseShape:
    """The unwrapped `LintConfig` declaration.

    Cached so repeated configuration parsing pays for one schema parse per
    process.
    """
    return schema_type(SCHEMA, ROOT)


def parse_config(text: str, registry: Registry, *, plugins: set[str] | None = None) -> Config:
    """Decode and validate one YAML configuration document.

    Structure first, then names. A document that is the wrong *shape* cannot
    have its names checked meaningfully — `rules: {}` has no entries to look
    up — so the type runs first and this returns on its failure.
    """
    raw = yaml.safe_load(text)
    if raw is None:
        raw = {}
    _check_category_keys(raw)
    failure = config_shape().validate(raw)
    if failure is not None:
        raise ValueError(str(failure).strip())

    extends = _strings(raw.get('extends', ['recommended']))
    for name in extends:
        if name not in registry.sets():
            raise ValueError(f'unknown ruleset: {name}')

    enabled_plugins = _strings(raw.get('plugins', []))
    available = plugins or set()
    for name in enabled_plugins:
        if name not in available:
            raise ValueError(f'unknown lint plugin: {name}')

    configured_categories: dict[str, RuleSetting] = {}
    for name, value in raw.get('categories', {}).items():
        if name not in registry.categories():
            raise ValueError(f'unknown rule category: {name}')
        configured_categories[name] = _setting(name, value)

    rules = []
    for value in raw.get('rules', []):
        rule_id = value['id']
        if registry.get(rule_id) is None:
            raise ValueError(f'unknown rule: {rule_id}')
        rules.append(_setting(rule_id, value))
    return Config(extends=extends, plugins=enabled_plugins, categories=configured_categories, rules=tuple(rules))


def _check_category_keys(value: object) -> None:
    """Keep YAML-only non-string keys away from RAML pattern matching."""
    if not isinstance(value, dict) or not isinstance(categories := value.get('categories'), dict):
        return
    for name in categories:
        if not isinstance(name, str):
            raise ValueError(f'unknown rule category: {name}')  # noqa: TRY004 - semantically an unknown name


def _strings(value: str | list[str]) -> tuple[str, ...]:
    """`extends` and `plugins` accept one name or several; the type allows both."""
    return (value,) if isinstance(value, str) else tuple(value)


def _setting(name: str, value: Mapping[str, Any]) -> RuleSetting:
    """One category or rule entry, with the regex compiled.

    Every field here has already been type-checked by `config.raml`; the only
    thing left that can fail is the regular expression, which no type can
    express.
    """
    match_raw = value.get('match')
    try:
        pattern = re.compile(match_raw) if match_raw is not None else None
    except re.error as err:
        raise ValueError(f'invalid match regex: {err}') from err
    severity = value.get('severity')
    return RuleSetting(
        id=name,
        severity=parse_severity(severity) if severity is not None else None,
        disabled=value.get('disabled'),
        match=pattern,
        options=dict(value.get('options', {})),
    )

"""Strict lint configuration decoding — docs/18-linting.md § 5."""

from __future__ import annotations

import re
from collections.abc import Mapping

import yaml

from fastraml.views.lint.engine import Config, Registry, RuleSetting, parse_severity

__all__ = ['parse_config']


def parse_config(text: str, registry: Registry, *, plugins: set[str] | None = None) -> Config:
    """Decode and validate one YAML configuration document."""
    raw = yaml.safe_load(text)
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise TypeError('lint config must be a mapping')
    if not all(isinstance(key, str) for key in raw):
        raise TypeError('lint config field names must be strings')
    unknown = set(raw) - {'extends', 'plugins', 'categories', 'rules'}
    if unknown:
        raise ValueError(f'unknown lint config field: {min(unknown)}')

    extends = _strings(raw.get('extends', ['recommended']), 'extends')
    for name in extends:
        if name not in registry.sets():
            raise ValueError(f'unknown ruleset: {name}')
    enabled_plugins = _strings(raw.get('plugins', []), 'plugins')
    available = plugins or set()
    for name in enabled_plugins:
        if name not in available:
            raise ValueError(f'unknown lint plugin: {name}')

    categories_raw = raw.get('categories', {})
    if not isinstance(categories_raw, Mapping):
        raise TypeError('categories must be a mapping')
    categories: dict[str, RuleSetting] = {}
    for name, value in categories_raw.items():
        if not isinstance(name, str) or name not in registry.categories():
            raise ValueError(f'unknown rule category: {name}')
        categories[name] = _setting(name, value, category=True)

    rules_raw = raw.get('rules', [])
    if not isinstance(rules_raw, list):
        raise TypeError('rules must be a list')
    rules = tuple(_rule_setting(value, registry) for value in rules_raw)
    return Config(extends=extends, plugins=enabled_plugins, categories=categories, rules=rules)


def _strings(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f'{field} must be a string or list of strings')
    return tuple(value)


def _rule_setting(value: object, registry: Registry) -> RuleSetting:
    if not isinstance(value, Mapping):
        raise TypeError('rule entry must be a mapping')
    rule_id = value.get('id')
    if not isinstance(rule_id, str) or not rule_id:
        raise ValueError('rule entry missing id')
    if registry.get(rule_id) is None:
        raise ValueError(f'unknown rule: {rule_id}')
    return _setting(rule_id, value)


def _setting(name: str, value: object, *, category: bool = False) -> RuleSetting:
    if not isinstance(value, Mapping):
        raise TypeError(f'{"category" if category else "rule"} setting must be a mapping')
    if not all(isinstance(key, str) for key in value):
        raise TypeError('setting field names must be strings')
    allowed = {'severity', 'disabled'} if category else {'id', 'severity', 'disabled', 'match', 'options'}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f'unknown setting field: {min(unknown)}')
    severity_raw = value.get('severity')
    if severity_raw is not None and not isinstance(severity_raw, str):
        raise ValueError('severity must be a string')
    disabled = value.get('disabled')
    if disabled is not None and not isinstance(disabled, bool):
        raise ValueError('disabled must be a boolean')
    match_raw = value.get('match')
    if match_raw is not None and not isinstance(match_raw, str):
        raise ValueError('match must be a string')
    options = value.get('options', {})
    if not isinstance(options, Mapping):
        raise TypeError('options must be a mapping')
    try:
        pattern = re.compile(match_raw) if match_raw is not None else None
    except re.error as err:
        raise ValueError(f'invalid match regex: {err}') from err
    return RuleSetting(
        id=name,
        severity=parse_severity(severity_raw) if severity_raw is not None else None,
        disabled=disabled,
        match=pattern,
        options=dict(options),
    )

"""Common FastRAML configuration shared by every parsing CLI verb."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import yaml

from fastraml.registry import DEFAULT_MAX_DEPTH, DEFAULT_MAX_INCLUDE_SIZE

if TYPE_CHECKING:
    import os
    from collections.abc import Mapping

    from fastraml.types.base import BaseShape

__all__ = [
    'CompatibilityConfig',
    'CompatibilityMatch',
    'CompatibilityRuleSetting',
    'FastRamlConfig',
    'ParserConfig',
    'load_config',
    'parse_config',
    'schema_type',
]

type Impact = Literal['breaking', 'review', 'compatible', 'cosmetic']

SCHEMA = Path(__file__).with_name('config.raml')
ROOT = 'FastRamlConfig'


@dataclass(frozen=True, slots=True)
class ParserConfig:
    workspace_root: str | None = None
    max_include_size: int = DEFAULT_MAX_INCLUDE_SIZE
    max_depth: int = DEFAULT_MAX_DEPTH
    regex_engine: Literal['re', 're2'] = 're'
    remote: bool = False


@dataclass(frozen=True, slots=True)
class CompatibilityMatch:
    operation: re.Pattern[str] | None = None
    location: str | None = None
    path: str | None = None
    subject: str | None = None
    attribute: str | None = None
    before_set: bool = False
    before: object = None
    after_set: bool = False
    after: object = None


@dataclass(frozen=True, slots=True)
class CompatibilityRuleSetting:
    id: str
    impact: Impact | None = None
    disabled: bool = False
    match: CompatibilityMatch | None = None


@dataclass(frozen=True, slots=True)
class CompatibilityConfig:
    rules: tuple[CompatibilityRuleSetting, ...] = ()


@dataclass(frozen=True, slots=True)
class FastRamlConfig:
    parser: ParserConfig = ParserConfig()
    lint: Mapping[str, object] = field(default_factory=dict)
    compatibility: CompatibilityConfig = CompatibilityConfig()


def schema_type(schema: Path, root: str) -> BaseShape:
    """The unwrapped declaration `root` in a configuration schema shipped as RAML.

    The parser import stays local, so importing a configuration module stays
    cheap until a configuration is validated. Callers cache the result.
    """
    from fastraml.parser.entry import ParseOptions, parse_from_path  # noqa: PLC0415 - deferred, see above

    raml = parse_from_path(schema, ParseOptions(unwrap=True, workspace_root=str(schema.parent)))
    types: Mapping[str, BaseShape] = getattr(raml.entry_point, 'types', {})
    shape = types.get(root)
    if shape is None:
        raise ValueError(f'{schema.name} declares no {root}')
    return shape


@lru_cache(maxsize=1)
def config_shape() -> BaseShape:
    """The unwrapped `FastRamlConfig` declaration, parsed once per process."""
    return schema_type(SCHEMA, ROOT)


def load_config(path: str | os.PathLike[str] | None) -> FastRamlConfig:
    if path is None:
        return FastRamlConfig()
    config_path = Path(path).resolve()
    return parse_config(config_path.read_text(encoding='utf-8'), base_dir=config_path.parent)


def parse_config(text: str, *, base_dir: Path | None = None) -> FastRamlConfig:
    try:
        raw = yaml.safe_load(text) if text else {}
    except yaml.YAMLError as err:
        raise ValueError(f'invalid YAML: {err}') from err
    if raw is None:
        raw = {}
    failure = config_shape().validate(raw)
    if failure is not None:
        raise ValueError(str(failure).strip())
    parser_raw = raw.get('parser', {})
    workspace = parser_raw.get('workspaceRoot')
    if workspace is not None and base_dir is not None and not Path(workspace).is_absolute():
        workspace = str(base_dir / workspace)
    parser = ParserConfig(
        workspace_root=workspace,
        max_include_size=parser_raw.get('maxIncludeSize', DEFAULT_MAX_INCLUDE_SIZE),
        max_depth=parser_raw.get('maxDepth', DEFAULT_MAX_DEPTH),
        regex_engine=parser_raw.get('regexEngine', 're'),
        remote=parser_raw.get('remote', False),
    )
    compatibility = CompatibilityConfig(
        rules=tuple(_compatibility_rule(value) for value in raw.get('compatibility', {}).get('rules', ()))
    )
    return FastRamlConfig(parser=parser, lint=dict(raw.get('lint', {})), compatibility=compatibility)


def _compatibility_rule(value: Mapping[str, object]) -> CompatibilityRuleSetting:
    raw_match = value.get('match')
    match = None
    if isinstance(raw_match, dict):
        operation = raw_match.get('operation')
        try:
            compiled = re.compile(operation) if isinstance(operation, str) else None
        except re.error as err:
            raise ValueError(f'invalid compatibility operation regex: {err}') from err
        match = CompatibilityMatch(
            operation=compiled,
            location=_string(raw_match.get('location')),
            path=_string(raw_match.get('path')),
            subject=_string(raw_match.get('subject')),
            attribute=_string(raw_match.get('attribute')),
            before_set='before' in raw_match,
            before=raw_match.get('before'),
            after_set='after' in raw_match,
            after=raw_match.get('after'),
        )
    return CompatibilityRuleSetting(
        id=str(value['id']),
        impact=cast('Impact | None', value.get('impact')),
        disabled=bool(value.get('disabled', False)),
        match=match,
    )


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None

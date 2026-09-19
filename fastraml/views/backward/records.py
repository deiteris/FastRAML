"""The JSON record, and the project policy that matches against it.

One module because they are one contract: `record` states the fields a consumer
reads, and an override in `fastraml.yaml` selects a change by those same fields.
A field that `record` stops emitting is a field no override can match, so the two
are kept where a reader sees both.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import TYPE_CHECKING

from fastraml.views.backward.model import (
    RULE_IDS,
    SUBJECTS,
    ApiChanged,
    ApiSchemaChanged,
    Change,
    OperationAdded,
    OperationLocation,
    OperationRemoved,
    PathSegment,
    SchemaChanged,
    _normal,
    _path_label,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastraml.config import CompatibilityConfig, CompatibilityMatch

__all__ = ['configure', 'record']


def configure(changes: Sequence[Change], config: CompatibilityConfig) -> list[Change]:
    """Apply ordered project overrides to compatibility results."""
    unknown = sorted({setting.id for setting in config.rules} - RULE_IDS)
    if unknown:
        raise ValueError(f'unknown compatibility rule: {unknown[0]}')
    # Checked for the same reason the rule id is: a `subject:` nobody emits matches
    # nothing, so a typo reads as a policy that silently never fires -- the one
    # failure an override must not have, since it is written to change a verdict.
    unknown_subjects = sorted(
        {setting.match.subject for setting in config.rules if setting.match and setting.match.subject} - SUBJECTS
    )
    if unknown_subjects:
        raise ValueError(f'unknown compatibility subject: {unknown_subjects[0]}')
    configured: list[Change] = []
    for original in changes:
        change: Change | None = original
        for setting in config.rules:
            if change is None or setting.id != change.rule or not _matches(change, setting.match):
                continue
            if setting.disabled:
                change = None
            elif setting.impact is not None:
                change = replace(change, impact=setting.impact)
        if change is not None:
            configured.append(change)
    return configured


def record(change: Change) -> dict[str, object]:
    if isinstance(change, ApiChanged):
        return {
            'scope': 'api',
            'kind': change.kind,
            'subject': change.subject,
            'attribute': change.attribute,
            'before': change.before,
            'after': change.after,
            'impact': change.impact,
            'rule': change.rule,
        }
    if isinstance(change, ApiSchemaChanged):
        return {
            'scope': 'api-schema',
            'kind': change.kind,
            'location': _location_record(change.location),
            'path': [_segment_record(segment) for segment in change.path],
            'subject': change.subject,
            'attribute': change.attribute,
            'before': change.before,
            'after': change.after,
            'impact': change.impact,
            'rule': change.rule,
        }
    base: dict[str, object] = {
        'operation': {'path': change.operation.path, 'method': change.operation.method},
        'impact': change.impact,
        'rule': change.rule,
    }
    if isinstance(change, OperationAdded):
        return base | {
            'kind': 'operation-added',
            'display_name': change.display_name,
            'description': change.description,
        }
    if isinstance(change, OperationRemoved):
        return base | {
            'kind': 'operation-removed',
            'display_name': change.display_name,
            'description': change.description,
        }
    detail = {
        'kind': change.kind,
        'location': _location_record(change.location),
        'subject': change.subject,
        'attribute': change.attribute,
        'before': change.before,
        'after': change.after,
    }
    if isinstance(change, SchemaChanged):
        return base | {'scope': 'schema', 'path': [_segment_record(segment) for segment in change.path]} | detail
    return base | {'scope': 'operation'} | detail


def _location_record(location: OperationLocation) -> dict[str, object]:
    return {'kind': type(location).__name__, **asdict(location)}


def _segment_record(segment: PathSegment) -> dict[str, object]:
    return {'kind': type(segment).__name__, **asdict(segment)}


def _matches(change: Change, match: CompatibilityMatch | None) -> bool:
    if match is None:
        return True
    operation = _operation_of(change)
    if match.operation is not None and (operation is None or match.operation.search(operation) is None):
        return False
    location, path, subject, attribute, before, after = _match_fields(change)
    return (
        (match.location is None or match.location == location)
        and (match.path is None or match.path == path)
        and (match.subject is None or match.subject == subject)
        and (match.attribute is None or match.attribute == attribute)
        and (not match.before_set or _normal(before) == _normal(match.before))
        and (not match.after_set or _normal(after) == _normal(match.after))
    )


def _operation_of(change: Change) -> str | None:
    if isinstance(change, (ApiChanged, ApiSchemaChanged)):
        return None
    return f'{change.operation.method.upper()} {change.operation.path}'


def _match_fields(change: Change) -> tuple[str, str | None, str, str | None, object, object]:
    if isinstance(change, ApiChanged):
        return 'Api', None, change.subject, change.attribute, change.before, change.after
    if isinstance(change, (OperationAdded, OperationRemoved)):
        # No `attribute` and no values. An operation that arrived or left has no
        # field that took a new value -- `rule` already separates `entity-added`
        # from `entity-removed`, and `match.operation` names which one. Inventing
        # an `availability: present` to match on would only be matchable by
        # someone who had read this function.
        return 'Operation', None, 'operation', None, None, None
    return (
        type(change.location).__name__,
        _path_label(change.path) if isinstance(change, (ApiSchemaChanged, SchemaChanged)) else None,
        change.subject,
        change.attribute,
        change.before,
        change.after,
    )

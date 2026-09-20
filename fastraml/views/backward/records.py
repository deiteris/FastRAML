"""The JSON record, and the project policy that matches against it.

One module because they are one contract: `record` states the fields a consumer
reads, and an override in `fastraml.yaml` selects a change by those same fields.
A field that `record` stops emitting is a field no override can match, so the two
are kept where a reader sees both.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Final, Literal, TypedDict

from fastraml.views.backward.model import (
    RULE_IDS,
    SUBJECTS,
    Change,
    OperationAdded,
    OperationRemoved,
    PathSegment,
    _normal,
    _path_label,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastraml.config import CompatibilityConfig, CompatibilityMatch
    from fastraml.views.backward.model import Impact, Location

__all__ = ['ChangeRecord', 'configure', 'record']


class ChangeRecord(TypedDict, total=False):
    """The JSON shape of one change, so a consumer types what it reads.

    `total=False` because the coordinate fields are the point: `operation` is
    absent for an API-level default and `path` for a change that is not inside a
    shape, and a consumer that assumes either is present is the bug this says
    out loud. `scope` names which combination it is, and is always present.
    """

    scope: Literal['api', 'api-schema', 'operation', 'schema']
    operation: dict[str, str]
    kind: str
    location: dict[str, object]
    path: list[dict[str, object]]
    subject: str
    attribute: str | None
    before: object
    after: object
    display_name: str | None
    description: str | None
    impact: Impact
    rule: str


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


#: `scope` names the cell of owner x path that a change sits in, for a consumer
#: that wants to filter without reading two optional fields. Derived, because
#: two fields and a name for their combination are one fact, not two.
_SCOPE: Final[dict[tuple[bool, bool], Literal['api', 'api-schema', 'operation', 'schema']]] = {
    (False, False): 'api',
    (False, True): 'api-schema',
    (True, False): 'operation',
    (True, True): 'schema',
}


def record(change: Change) -> ChangeRecord:
    """One JSON object per change. `api` omits `operation`; a contract omits `path`.

    A field is absent rather than null when the change has no such coordinate,
    because `"path": null` reads as "at no path" where the truth is "this is not
    a change inside a shape at all". `path` is `[]` at a shape root.
    """
    if isinstance(change, (OperationAdded, OperationRemoved)):
        return {
            'operation': {'path': change.operation.path, 'method': change.operation.method},
            'kind': 'operation-added' if isinstance(change, OperationAdded) else 'operation-removed',
            'display_name': change.display_name,
            'description': change.description,
            'impact': change.impact,
            'rule': change.rule,
        }
    out: ChangeRecord = {'scope': _SCOPE[change.operation is not None, change.path is not None]}
    if change.operation is not None:
        out['operation'] = {'path': change.operation.path, 'method': change.operation.method}
    out['kind'] = change.kind
    out['location'] = _location_record(change.location)
    if change.path is not None:
        out['path'] = [_segment_record(segment) for segment in change.path]
    out['subject'] = change.subject
    out['attribute'] = change.attribute
    out['before'] = change.before
    out['after'] = change.after
    out['impact'] = change.impact
    out['rule'] = change.rule
    return out


def _location_record(location: Location) -> dict[str, object]:
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
    operation = change.operation
    return None if operation is None else f'{operation.method.upper()} {operation.path}'


def _match_fields(change: Change) -> tuple[str, str | None, str, str | None, object, object]:
    """What an override matches on, in the spelling a project writes.

    `location` is the coordinate's class name for every change that has one --
    including an API-level default, which used to report the string `Api` and so
    matched no `location:` a reader could have guessed. `docs/13` and the
    `backward` skill both show `location: TransportLocation` against exactly that
    case.
    """
    if isinstance(change, (OperationAdded, OperationRemoved)):
        # No `attribute` and no values. An operation that arrived or left has no
        # field that took a new value -- `rule` already separates `entity-added`
        # from `entity-removed`, and `match.operation` names which one. Inventing
        # an `availability: present` to match on would only be matchable by
        # someone who had read this function.
        return 'Operation', None, 'operation', None, None, None
    return (
        type(change.location).__name__,
        None if change.path is None else _path_label(change.path),
        change.subject,
        change.attribute,
        change.before,
        change.after,
    )

"""The reading view: one comparison as Markdown, grouped the way a caller reads.

Separate from the change list on purpose. The list is the contract -- walk order,
one entry per operation, matchable by a project override -- and this is a
document: rolled up so one edit reaching four operations is one row, split by
side of the wire and by kind, sorted worst first, and carrying no column every
row in its table leaves blank.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from fastraml.views.backward.model import (
    IMPACTS,
    ApiChanged,
    ApiLocation,
    ApiSchemaChanged,
    Change,
    ChangeKind,
    Direction,
    Impact,
    LocatedChange,
    OperationAdded,
    OperationChanged,
    OperationId,
    OperationLocation,
    OperationRemoved,
    ParameterLocation,
    RequestBody,
    ResponseBody,
    ResponseStatus,
    SchemaChanged,
    SecurityLocation,
    Subject,
    TransportLocation,
    _normal,
    _path_label,
    side_of,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ['render_markdown']


def render_markdown(changes: Sequence[Change]) -> str:
    """The reading view: grouped the way a caller reads, not the way the walk built it.

    Each operation is split into what it sends and what it receives, because those
    are a consumer's two questions and the answer to each is actionable on its
    own. Grouping by result type instead put a parameter's requiredness and its
    type in different tables under different headings, so "what happened to
    `limit`?" took two lookups and an understanding of which Python class
    produced which row.

    Rows within a table are sorted by impact, stably, so a break never sits below
    a documentation edit and same-impact rows keep the order the document declared
    them in. The sort lives here and not in `backward` because `impact` is the one
    field `configure` rewrites -- ordering the change list by a grading a project
    is free to override would leave the list stale the moment one did.
    """
    api = [change for change in changes if isinstance(change, (ApiChanged, ApiSchemaChanged))]
    added = [change for change in changes if isinstance(change, OperationAdded)]
    removed = [change for change in changes if isinstance(change, OperationRemoved)]
    shared, owned_by = _rollup(changes)
    entries: list[Change] = [*api, *added, *removed, *(entry.change for entry in shared)]
    entries.extend(change for owned in owned_by.values() for change in owned)
    counts = {impact: sum(entry.impact == impact for entry in entries) for impact in IMPACTS.order}
    result = 'Breaking' if counts['breaking'] else ('Review required' if counts['review'] else 'Compatible')
    callout = 'CAUTION' if counts['breaking'] else ('WARNING' if counts['review'] else 'TIP')
    lines = [
        '# API compatibility',
        '',
        f'> [!{callout}]',
        f'> **{result}.** {_verdict_sentence(counts)}',
        '',
        '| Impact | Changes |',
        '|---|---:|',
        f'| Breaking | {counts["breaking"]} |',
        f'| Review required | {counts["review"]} |',
        f'| Compatible | {counts["compatible"]} |',
        f'| Documentation | {counts["cosmetic"]} |',
        *_LEGEND,
    ]
    if api:
        lines.extend(('', '## Every operation', '', 'Declared once at the API root, so these reach every operation.'))
        lines.extend(_sided_tables([_Entry(change) for change in api]))
    if removed or added:
        lines.extend(('', '## Operations added and removed'))
    if removed:
        lines.extend(('', '### Removed', ''))
        lines.extend(_operation_markdown(change) for change in removed)
    if added:
        lines.extend(('', '### Added', ''))
        lines.extend(_operation_markdown(change) for change in added)
    if shared:
        lines.extend(('', '## Several operations', '', 'One edit each, reaching the operations named on the row.'))
        lines.extend(_sided_tables(shared))
    for operation, owned in owned_by.items():
        lines.extend(('', f'## {_inline_code(f"{operation.method.upper()} {operation.path}")}'))
        lines.extend(_sided_tables([_Entry(change) for change in owned]))
    return '\n'.join(lines) + '\n'


@dataclass(frozen=True, slots=True)
class _Entry:
    """A row, and every operation it says the same thing about."""

    change: LocatedChange
    operations: tuple[OperationId, ...] = ()


def _rollup(changes: Sequence[Change]) -> tuple[list[_Entry], dict[OperationId, list[LocatedChange]]]:
    """One edit reaching many operations is one row, not one row per operation.

    The change *list* is right to hold them apart: a caller of `/orders` and a
    caller of `/invoices` hold different contracts, and `configure` matches each
    on its own operation. But a type shared by four of them, edited once, produced
    an identical row under every one -- four tables and a headline of "4 breaking
    changes" for a single `maxLength`, which counts the blast radius as though it
    were the number of decisions a reader has to make.

    Grouped on everything except the owner, so only rows stating the same fact
    collapse. A change reaching one operation stays under it.
    """
    groups: dict[object, list[OperationChanged | SchemaChanged]] = {}
    for change in changes:
        if isinstance(change, (OperationChanged, SchemaChanged)):
            groups.setdefault(_shared_key(change), []).append(change)
    shared: list[_Entry] = []
    owned_by: dict[OperationId, list[LocatedChange]] = {}
    for members in groups.values():
        if len(members) > 1:
            shared.append(_Entry(members[0], tuple(change.operation for change in members)))
        else:
            owned_by.setdefault(members[0].operation, []).append(members[0])
    return shared, owned_by


def _shared_key(change: OperationChanged | SchemaChanged) -> object:
    path = change.path if isinstance(change, SchemaChanged) else ()
    return (
        change.location,
        path,
        change.kind,
        change.subject,
        change.attribute,
        _normal(change.before),
        _normal(change.after),
        change.impact,
        change.rule,
    )


#: What a section heading means, stated once. These are definitions rather than
#: findings, so they belong where a reader meets them first and nowhere else --
#: under every table they would be eighty lines saying the same six things.
_LEGEND: Final[tuple[str, ...]] = (
    '',
    '## How to read this',
    '',
    'Each operation is split into what a caller **sends** and what it **receives**,',
    'then into what was removed, changed and added on that side. Worst first.',
    '',
    '| Section | Request | Response |',
    '|---|---|---|',
    '| **Removed** | the API no longer reads it | the API no longer returns it |',
    '| **Changed** | the old form may now be rejected | a value you did not expect may arrive |',
    '| **Added** | new, and breaking only if required | returned as well; ignore it and nothing breaks |',
    '',
    'A row names **Where** the change is, and a **Path** when it reaches inside a',
    'shape. The value column is headed by what it holds -- **Type**, **Value**,',
    '**Scheme** -- or **Detail** where a table mixes them, in which case a **What**',
    'column says which each row is. A change states `old -> new`. Columns a table',
    'has no use for are left out of it.',
)

#: Request before response, because a caller fixes what it sends before it can
#: see what it receives. `None` is the documentation bucket: prose that changed
#: on no side of the wire, kept out of both so neither reads as actionable.
_SIDES: Final[tuple[tuple[Direction | None, str], ...]] = (
    ('request', 'Request'),
    ('response', 'Response'),
    (None, 'Documentation'),
)


#: Worst first within a side, so "read the breaking things first" survives the
#: split into kinds. `Removed` before `Changed` before `Added` only breaks ties.
_KINDS: Final[tuple[ChangeKind, ...]] = ('removed', 'changed', 'added')


def _sided_tables(entries: Sequence[_Entry]) -> list[str]:
    """One table per side and kind, and no column that every row leaves blank.

    Split by kind because `Before` and `After` fitted only one of the three. An
    addition has no before and a removal has no after, so half the worked
    catalogue's rows spent a two-column pair to carry one value -- and eight of
    them carried none, their coordinate having said it all. A `Changed` table
    states its transition in one `Detail` cell; an `Added` or `Removed` one needs
    no `Change` column at all, because its heading is the verb.

    What each kind means for a caller is a definition, not a fact about this
    comparison, so it is stated once in the legend rather than under forty
    tables.
    """
    lines: list[str] = []
    for side, heading in _SIDES:
        rows = [entry for entry in entries if side_of(entry.change.location) == side]
        if not rows:
            continue
        lines.extend(('', f'### {heading}'))
        groups = [(kind, [row for row in rows if row.change.kind == kind]) for kind in _KINDS]
        for kind, group in sorted(
            (pair for pair in groups if pair[1]),
            key=lambda pair: min(IMPACTS.rank(row.change.impact) for row in pair[1]),
        ):
            lines.extend(('', f'**{kind.title()}**', ''))
            lines.extend(_kind_table(group, side_stated=side is not None, transition=kind == 'changed'))
    return lines


#: What the value cell holds, per subject. It is not one thing: a property's is a
#: type, an enum member's is a value and a scheme's is a name, so heading all
#: three `Detail` made the reader work out which. It matters most for an enum,
#: whose `Where` and `Path` address the *property* rather than the thing that
#: moved -- the one row in the report where the coordinate is not the subject.
_VALUE_NOUN: Final[dict[Subject, str]] = {
    'parameter': 'Type',
    'pattern-property': 'Type',
    'property': 'Type',
    'enum-value': 'Value',
    'security-alternative': 'Scheme',
}


def _kind_table(rows: Sequence[_Entry], *, side_stated: bool, transition: bool) -> list[str]:
    paths = any(isinstance(row.change, (ApiSchemaChanged, SchemaChanged)) and row.change.path for row in rows)
    details = any(_detail(row.change) for row in rows)
    described = any(_description(row.change) for row in rows)
    operations = any(row.operations for row in rows)
    # A `changed` row holds a transition whatever its subject, and its `Change`
    # column has already named the facet. Only the other two kinds get the noun,
    # and only where every row that fills the cell agrees on one; a mixed table
    # falls back to `Detail` and earns a `What` column to say which is which.
    nouns = {_VALUE_NOUN.get(row.change.subject, 'Detail') for row in rows if _detail(row.change)}
    subjects = not transition and len(nouns) > 1
    value = 'Detail' if transition or len(nouns) != 1 else nouns.pop()
    columns = ['Where', *(['Path'] if paths else []), *(['Change'] if transition else [])]
    columns.extend([*(['What'] if subjects else []), *([value] if details else [])])
    columns.extend([*(['Description'] if described else [])])
    columns.extend(['Compatibility', *(['Operations'] if operations else [])])
    return [
        f'| {" | ".join(columns)} |',
        f'|{"---|" * len(columns)}',
        *(
            _change_row(
                entry,
                side_stated=side_stated,
                paths=paths,
                change=transition,
                subjects=subjects,
                details=details,
                described=described,
                operations=operations,
            )
            for entry in _by_impact(rows)
        ),
    ]


def _by_impact(entries: Sequence[_Entry]) -> list[_Entry]:
    """Most costly first, ties in declaration order -- `sorted` is stable."""
    return sorted(entries, key=lambda entry: IMPACTS.rank(entry.change.impact))


def _change_row(  # noqa: PLR0913 - one flag per column the table decided to carry
    entry: _Entry,
    *,
    side_stated: bool,
    paths: bool,
    change: bool,
    subjects: bool,
    details: bool,
    described: bool,
    operations: bool,
) -> str:
    """One row shape for all four results: they differ in owner, not in reading.

    Every column is optional except `Where` and `Compatibility`, and the table
    decides which it carries by asking whether any of its rows fills one. `Path`
    is empty whenever the change is to the coordinate itself -- both for a
    contract change, which has no path, and for a shape change at the root, whose
    `$` says "the thing in Where". A path appears when it reaches *inside*, and
    then it is anchored at `$` because `$.title` needs somewhere to hang.
    """
    result = entry.change
    cells = [_cell(_location_label(result.location, side_stated=side_stated))]
    if paths:
        inside = ''
        if isinstance(result, (ApiSchemaChanged, SchemaChanged)) and result.path:
            inside = _cell(_inline_code(_path_label(result.path)))
        cells.append(inside)
    if change:
        cells.append(_cell(_change_label(result)))
    if subjects:
        cells.append(result.subject.replace('-', ' ').capitalize())
    if details:
        cells.append(_cell(_detail(result)))
    if described:
        cells.append(_cell(_plain_inline(_summary(_description(result)))))
    cells.append(result.impact.title())
    if operations:
        cells.append(
            ', '.join(_inline_code(f'{operation.method.upper()} {operation.path}') for operation in entry.operations)
        )
    return f'| {" | ".join(cells)} |'


def _description(change: LocatedChange) -> str:
    """What the author said the added or removed thing is for, if anything.

    Its own column rather than folded into `Detail`, because a type and a
    requiredness are what to send and this is what it means -- a reader scanning
    a list of new fields wants the second without re-reading the first. It is the
    one thing an addition can say that its coordinate cannot, and it reaches every
    entity RAML lets an author describe: properties, query parameters, headers,
    response statuses, bodies, union members and security schemes.
    """
    value = change.after if change.after is not None else change.before
    return str(value['description']) if isinstance(value, dict) and 'description' in value else ''


def _detail(change: LocatedChange) -> str:
    """The values, in one cell: a transition for a change, a descriptor otherwise.

    `Before` and `After` fitted a `changed` row and misfitted the other two,
    where one side is empty by construction and the heading already says which.
    A removed body, response or union member fills neither, because its
    coordinate is the whole fact -- and a table of those drops this column too.
    """
    before = _markdown_value(change, change.before) if change.before is not None else ''
    after = _markdown_value(change, change.after) if change.after is not None else ''
    if change.kind != 'changed':
        return before or after
    return f'{before or "Absent"} -> {after or "Absent"}'


def _operation_markdown(change: OperationAdded | OperationRemoved) -> str:
    parts = [_inline_code(f'{change.operation.method.upper()} {change.operation.path}')]
    if change.display_name is not None:
        parts.append(f'**{_plain_inline(_summary(change.display_name))}**')
    if change.description is not None:
        parts.append(_plain_inline(_summary(change.description)))
    return '- ' + ' - '.join(parts)


def _location_label(  # noqa: PLR0911 - one spelling per location variant
    location: ApiLocation | OperationLocation, *, side_stated: bool = False
) -> str:
    """Where the change is, without the part the reader has already been told.

    `side_stated` is true under a **Request** or **Response** heading, which has
    named the side for every row beneath it. Repeating it turned each cell into
    "Response `200` header `X-Trace`" under a section already headed Response --
    the word twice on one line, and the column's widest cells spent on it.

    A response keeps its status, which the heading does not state. A request body
    keeps nothing but the media type, which is all that was ever distinguishing.
    """
    side = '' if side_stated else 'Response '
    if isinstance(location, RequestBody):
        return f'{"Body" if side_stated else "Request body"} {_inline_code(location.media_type)}'
    if isinstance(location, ResponseBody):
        return f'{side}{_inline_code(location.status)} body {_inline_code(location.media_type)}'
    if isinstance(location, ResponseStatus):
        return f'{"Status" if side_stated else "Response"} {_inline_code(location.status)}'
    if isinstance(location, ParameterLocation):
        prefix = f'{side}{_inline_code(location.response_status)} ' if location.response_status else ''
        # A header is a header; only the other three bindings are RAML "parameters".
        noun = 'header' if location.binding == 'header' else f'{location.binding} parameter'
        return f'{prefix}{noun} {_inline_code(location.name)}'
    if isinstance(location, SecurityLocation):
        return 'Security'
    if isinstance(location, TransportLocation):
        return 'Transport'
    return 'Operation'


def _change_label(change: LocatedChange) -> str:
    """The Change column: an attribute is an identifier, a kind is prose.

    Every `attribute` is a name -- a RAML facet, a security setting, a node the
    author wrote -- so all of them read as code and none of them read as a
    sentence. That keeps one column able to hold `maxLength` next to
    `Property removed` without the reader deciding which is which.
    """
    if change.attribute == 'required':
        # "Requiredness" is the property's own word. Under `security` the thing
        # that became required is a credential, and naming it the same way left a
        # reader to guess what was optional before.
        return 'Authentication' if change.subject == 'security' else 'Requiredness'
    if change.attribute is not None and change.kind == 'changed':
        return _inline_code(change.attribute)
    return f'{change.subject.replace("-", " ").capitalize()} {change.kind}'


def _value(subject: Subject, value: object) -> str:
    r"""One value of `subject`, spelled the way the RAML document spells it.

    Dispatching on `subject` and not on the Python type is the point. `True` is
    "Required" under `required` and `true` under `constraint`, and a renderer that
    reads the runtime type alone cannot tell those apart -- it reported
    `additionalProperties: true -> false` as "Required -> Optional".

    What the document states is fenced as code; a word this report chose for a
    state -- Absent, Required, None -- is not, so the fence is the reader's answer
    to "did the author write that?". `pattern` is why it matters: escaped as text,
    `^[A-Z]+$` renders `^\\[A-Z\\]+$`, which is a different regex and leaves no way
    to tell the author's backslashes from Markdown's.
    """
    if value is None:
        return 'Absent'
    if isinstance(value, bool):
        if subject in ('required', 'security'):
            return 'Required' if value else 'Optional'
        return _inline_code('true' if value else 'false')
    if isinstance(value, dict):
        # A descriptor carries any subset of name, requiredness and type, and its
        # `description` belongs to its own column. Missing keys are not defaults:
        # a removed body states no requiredness because a body has none.
        parts = [_inline_code(str(value['name']))] if 'name' in value else []
        parts.extend(['required' if value['required'] else 'optional'] if 'required' in value else [])
        parts.extend([_inline_code(str(value['type']))] if 'type' in value else [])
        return ' '.join(parts)
    if isinstance(value, tuple):
        return ', '.join(_inline_code(_scalar(item)) for item in value) or 'None'
    return _inline_code(_scalar(value))


def _scalar(value: object) -> str:
    """A leaf as RAML spells it: `true`, `false` and `null`, not Python's."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _markdown_value(change: LocatedChange, value: object) -> str:
    # An added or removed subject has one populated side by construction, and the
    # label already says which. Printing "Absent" opposite it states the same fact
    # twice for a property, and states a false one for an enum member, whose empty
    # side means "no members arrived" rather than "the enum is gone".
    if value is None and change.kind != 'changed':
        return ''
    if change.subject == 'documentation' and value is not None:
        # The one subject whose value is prose rather than something to copy: an
        # author's sentence is escaped as text, and a description is summarised
        # first, because the column holds a line and the record holds the rest.
        text = str(value)
        return _plain_inline(_summary(text) if change.attribute == 'description' else text)
    return _value(change.subject, value)


def _cell(value: str) -> str:
    return ' '.join(value.splitlines()).replace('\t', ' ').replace('|', '\\|')


def _verdict_sentence(counts: Mapping[Impact, int]) -> str:
    if counts['breaking']:
        count = counts['breaking']
        return f'{count} breaking change{"s" if count != 1 else ""} require action before release.'
    if counts['review']:
        count = counts['review']
        return f'{count} change{"s" if count != 1 else ""} require manual review.'
    return 'No existing caller is broken by the reported changes.'


_SUMMARY_LIMIT: Final = 160
_PLAIN_MARKDOWN_ESCAPES: Final = str.maketrans({char: f'\\{char}' for char in r'\\`*_{}[]<>'})


def _summary(value: str) -> str:
    lines = [line.strip() for line in value.splitlines()]
    nonempty = [line for line in lines if line]
    if not nonempty:
        return ''
    first = nonempty[0]
    truncated = len(nonempty) > 1 or len(first) > _SUMMARY_LIMIT
    if len(first) > _SUMMARY_LIMIT:
        first = first[: _SUMMARY_LIMIT - 3].rstrip()
    return first + ('...' if truncated else '')


def _plain_inline(value: str) -> str:
    return value.translate(_PLAIN_MARKDOWN_ESCAPES)


def _inline_code(value: str) -> str:
    value = ' '.join(value.splitlines())
    fence = '`'
    while fence in value:
        fence += '`'
    padding = ' ' if value.startswith(('`', ' ')) or value.endswith(('`', ' ')) else ''
    return f'{fence}{padding}{value}{padding}{fence}'

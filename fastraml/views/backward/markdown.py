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
    Change,
    Changed,
    ChangeKind,
    Direction,
    Impact,
    Location,
    OperationAdded,
    OperationId,
    OperationRemoved,
    ParameterLocation,
    RequestBody,
    ResponseBody,
    ResponseStatus,
    SecurityLocation,
    Subject,
    TransportLocation,
    TypeDeclaration,
    _normal,
    _path_label,
    side_of,
    side_of_rule,
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
    api = [
        change
        for change in changes
        if isinstance(change, Changed) and change.operation is None and not isinstance(change.location, TypeDeclaration)
    ]
    added = [change for change in changes if isinstance(change, OperationAdded)]
    removed = [change for change in changes if isinstance(change, OperationRemoved)]
    shared, owned_by = _rollup(changes)
    declared = _declaration_rows(changes)
    # Counted per *row*, not per record: one edit reaching four operations is one
    # decision, and a declaration graded for both sides is one edit with two
    # answers. The exit code still follows the records, which is `cli`'s job.
    graded: list[Impact] = [
        *(change.impact for change in api),
        *(change.impact for change in added),
        *(change.impact for change in removed),
        *(entry.change.impact for entry in shared),
        *(change.impact for owned in owned_by.values() for change in owned),
        *(row.worst() for row in declared),
    ]
    counts = {impact: graded.count(impact) for impact in IMPACTS.order}
    result = 'Breaking' if counts['breaking'] else ('Review required' if counts['review'] else 'Compatible')
    callout = 'CAUTION' if counts['breaking'] else ('WARNING' if counts['review'] else 'TIP')
    operational = bool(api or added or removed or shared or owned_by)
    lines = [
        '# API compatibility' if operational or not declared else '# Type compatibility',
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
        *(_LEGEND if operational or not declared else _TYPE_LEGEND),
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
    lines.extend(_declaration_tables(declared))
    return '\n'.join(lines) + '\n'


@dataclass(frozen=True, slots=True)
class _Entry:
    """A row: one change, plus what else it says the same thing about.

    `operations` is every operation one shared edit reaches. `counterpart` is
    the other half of a two-sided verdict -- a type declaration is graded once
    for a sender and once for a reader, and those are two changes in the list
    but one row to read, because they are one edit.
    """

    change: Changed
    operations: tuple[OperationId, ...] = ()
    counterpart: Changed | None = None

    def worst(self) -> Impact:
        """The row's grade: the worse half, so a break never hides behind a pass."""
        if self.counterpart is None:
            return self.change.impact
        return min((self.change.impact, self.counterpart.impact), key=IMPACTS.rank)


def _rollup(changes: Sequence[Change]) -> tuple[list[_Entry], dict[OperationId, list[Changed]]]:
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
    groups: dict[object, list[Changed]] = {}
    for change in changes:
        if isinstance(change, Changed) and change.operation is not None:
            groups.setdefault(_shared_key(change), []).append(change)
    shared: list[_Entry] = []
    owned_by: dict[OperationId, list[Changed]] = {}
    for members in groups.values():
        owner = members[0].operation
        if len(members) > 1:
            # Narrowed above, and every member of a group shares the key that
            # excludes the owner -- so each has one, and each has a different one.
            shared.append(_Entry(members[0], tuple(m.operation for m in members if m.operation is not None)))
        elif owner is not None:
            owned_by.setdefault(owner, []).append(members[0])
    return shared, owned_by


def _shared_key(change: Changed) -> object:
    return (
        change.location,
        change.path,
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

#: The same definitions for a report with no operations in it. A declared type
#: is on neither side of the wire, so the split a caller reads by is not
#: Request/Response down the page but two grades across each row.
_TYPE_LEGEND: Final[tuple[str, ...]] = (
    '',
    '## How to read this',
    '',
    'These are `types:` declarations, compared as declarations. A type is neither',
    'sent nor received until something uses it, so each row carries two grades.',
    '',
    '| Column | Means |',
    '|---|---|',
    '| **If sent** | what the change does to code that *produces* a value of this type |',
    '| **If received** | what it does to code that *reads* one |',
    '',
    'Tightening a constraint rejects producers and reassures readers; loosening one',
    'does the reverse. A row is listed under the worse of its two grades, so nothing',
    'breaking sits below something safe.',
    '',
    'A row names **Where** the change is, and a **Path** when it reaches inside a',
    'shape. The value column is headed by what it holds -- **Type**, **Value** --',
    'or **Detail** where a table mixes them. A change states `old -> new`. Columns',
    'a table has no use for are left out of it.',
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
    # A declaration is on neither side of the wire, so one grade would have to
    # pick one and be wrong for the other reader. Two columns state both.
    sided = any(row.counterpart is not None for row in rows)
    wheres = any(_location_label(row.change.location, side_stated=side_stated) for row in rows)
    paths = any(row.change.path for row in rows)
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
    columns = [*(['Where'] if wheres else []), *(['Path'] if paths else []), *(['Change'] if transition else [])]
    columns.extend([*(['What'] if subjects else []), *([value] if details else [])])
    columns.extend([*(['Description'] if described else [])])
    columns.extend(['If sent', 'If received'] if sided else ['Compatibility'])
    columns.extend(['Operations'] if operations else [])
    return [
        f'| {" | ".join(columns)} |',
        f'|{"---|" * len(columns)}',
        *(
            _change_row(
                entry,
                side_stated=side_stated,
                wheres=wheres,
                paths=paths,
                change=transition,
                subjects=subjects,
                details=details,
                described=described,
                operations=operations,
                sided=sided,
            )
            for entry in _by_impact(rows)
        ),
    ]


def _declaration_tables(rows: Sequence[_Entry]) -> list[str]:
    """One section per declared type, graded for a sender and for a reader.

    A declaration is not on the wire, so there is no Request/Response split to
    make here: the *row* carries both answers instead of the section doing it.
    Kinds still split, because "what left" and "what arrived" are still two
    questions.
    """
    if not rows:
        return []
    lines = [
        '',
        '## Type declarations',
        '',
        'One section per declared type. Each row is graded both ways, because a',
        'type is neither sent nor received until something uses it.',
    ]
    for name in dict.fromkeys(_declared_name(row) for row in rows):
        owned = [row for row in rows if _declared_name(row) == name]
        lines.extend(('', f'### {_inline_code(name)}'))
        for kind, group in sorted(
            ((kind, [row for row in owned if row.change.kind == kind]) for kind in _KINDS),
            key=lambda pair: min((IMPACTS.rank(row.worst()) for row in pair[1]), default=len(IMPACTS.order)),
        ):
            if not group:
                continue
            lines.extend(('', f'**{kind.title()}**', ''))
            lines.extend(_kind_table(group, side_stated=True, transition=kind == 'changed'))
    return lines


def _declared_name(entry: _Entry) -> str:
    location = entry.change.location
    return location.name if isinstance(location, TypeDeclaration) else ''


def _halves(entry: _Entry) -> tuple[Impact, Impact]:
    """The row's two grades, sender first, read from which side each rule names."""
    pair = [entry.change, *([] if entry.counterpart is None else [entry.counterpart])]
    by_side = {side_of_rule(change.rule): change.impact for change in pair}
    only = entry.change.impact
    return by_side.get('request', only), by_side.get('response', only)


def _declaration_rows(changes: Sequence[Change]) -> list[_Entry]:
    """The two halves of each declaration verdict, paired into one row.

    They are two changes on purpose -- each carries the single `impact` that
    `configure` overrides and the exit code reads -- and one row on purpose,
    because they are one edit and a reader deciding whether to publish wants
    both answers side by side.
    """
    grouped: dict[object, list[Changed]] = {}
    for change in changes:
        if isinstance(change, Changed) and isinstance(change.location, TypeDeclaration):
            key = (
                change.location,
                change.path,
                change.kind,
                change.subject,
                change.attribute,
                _normal(change.before),
                _normal(change.after),
            )
            grouped.setdefault(key, []).append(change)
    rows: list[_Entry] = []
    for members in grouped.values():
        first = next((c for c in members if side_of_rule(c.rule) == 'request'), members[0])
        other = next((c for c in members if c is not first), None)
        rows.append(_Entry(first, counterpart=other))
    return rows


def _by_impact(entries: Sequence[_Entry]) -> list[_Entry]:
    """Most costly first, ties in declaration order -- `sorted` is stable."""
    return sorted(entries, key=lambda entry: IMPACTS.rank(entry.worst()))


def _change_row(  # noqa: PLR0913 - one flag per column the table decided to carry
    entry: _Entry,
    *,
    side_stated: bool,
    paths: bool,
    wheres: bool = True,
    change: bool,
    subjects: bool,
    details: bool,
    described: bool,
    operations: bool,
    sided: bool = False,
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
    cells = [_cell(_location_label(result.location, side_stated=side_stated))] if wheres else []
    if paths:
        inside = ''
        if result.path:
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
    if sided:
        # `request` first, because a caller fixes what it sends before it can see
        # what it receives -- the same order the wire sections are in.
        sent, received = _halves(entry)
        cells.extend([sent.title(), received.title()])
    else:
        cells.append(result.impact.title())
    if operations:
        cells.append(
            ', '.join(_inline_code(f'{operation.method.upper()} {operation.path}') for operation in entry.operations)
        )
    return f'| {" | ".join(cells)} |'


def _description(change: Changed) -> str:
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


def _detail(change: Changed) -> str:
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
    location: Location, *, side_stated: bool = False
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
    if isinstance(location, TypeDeclaration):
        # Under `### Money` the heading has named it, exactly as a Request or
        # Response heading names the side -- so the cell is empty and the column
        # drops out. A row addressing the whole type still needs the name.
        return '' if side_stated else _inline_code(location.name)
    if isinstance(location, SecurityLocation):
        return 'Security'
    if isinstance(location, TransportLocation):
        return 'Transport'
    return 'Operation'


def _change_label(change: Changed) -> str:
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


def _markdown_value(change: Changed, value: object) -> str:
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

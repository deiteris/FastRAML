"""The `python-httpx` target's spellings: `TypedDict`s, and the checks that read them.

The traversal is in `../shared/annotate.py`. What is here is the six hooks
and the one thing only this target has to solve — telling a union's members
apart on the way in, with nothing but the value and what the document said.

**A value is its JSON.** A model is a `TypedDict` keyed by the property names as
the document spells them, so `@odata.type`, `$ref` and `class` need no Python
name, and nothing is converted in either direction. `decode` is a *check*
instead. It reads a decoded payload, reports what the document requires and the
payload lacks, and changes nothing; the value it was handed is the value the
caller gets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ....naming import Names, module_name
from ..shared.annotate import IDENTITY, Annotation, Annotator, fill

if TYPE_CHECKING:
    from ....reader import Tree
    from ....tree import Shape
    from ..shared.annotate import Member

__all__ = ['CHECKS', 'PythonAnnotator', 'make_annotator', 'reader']

#: The runtime names only a check calls. An annotation whose check a module
#: does not run needs its spelling imported and none of these.
CHECKS = frozenset({'as_list', 'each'})


def _runtime(name: str) -> Annotation:
    return Annotation(name, runtime=frozenset({name}))


#: The scalar kinds, and what each one is in JSON. A bound, a pattern or a
#: `multipleOf` is a constraint rather than a type, so it reaches the docstring
#: and not the annotation. A date stays the string it arrived as, under a name
#: from `types.py` that says which string: parsing it is the caller's.
_SCALARS: dict[str, Annotation] = {
    'any': Annotation('Any', imports=frozenset({'Any'})),
    'nil': Annotation('None'),
    'null': Annotation('None'),
    'boolean': Annotation('bool'),
    'string': Annotation('str'),
    'integer': Annotation('int'),
    'number': Annotation('float'),
    'file': _runtime('File'),
    'datetime': _runtime('DateTime'),
    'datetime-only': _runtime('DateTimeOnly'),
    'date-only': _runtime('DateOnly'),
    'time-only': _runtime('TimeOnly'),
}

#: A `datetime` whose `format:` is `rfc2616` is written the way an HTTP header
#: writes one, and needs a different parser.
_HTTP_DATE = _runtime('HttpDate')

_ANY = Annotation('Any', imports=frozenset({'Any'}))
_MAPPING = Annotation('dict[str, Any]', imports=frozenset({'Any'}))


@dataclass(slots=True)
class PythonAnnotator(Annotator):
    """Shapes as `TypedDict`s, with a reader beside each that checks one."""

    #: Each model's reader, by the model's name. A `Names` because two class
    #: names may share a snake-case spelling, and their readers must not.
    readers: Names = field(default_factory=Names)

    def scalar(self, kind: str, shape: Shape | None = None) -> Annotation:
        # A bound, a pattern or a `multipleOf` is documentation here. The one
        # facet read is a date's `format:`, which says which string it is.
        if kind == 'datetime' and shape is not None and shape.get('format') == 'rfc2616':
            return _HTTP_DATE
        return _SCALARS.get(kind, _ANY)

    def mapping(self) -> Annotation:
        return _MAPPING

    def enum(self, shape: Shape) -> Annotation:
        """Spell a closed set of values as a `Literal` rather than a class.

        RAML's `enum:` lists values and attaches no names to them. An `Enum`
        subclass would have to invent the names, and an identifier for
        `9780441013593` is guesswork a reader cannot check.
        """
        members = ', '.join(repr(value) for value in shape.get('enum', ()))
        return Annotation(f'Literal[{members}]', imports=frozenset({'Literal'}))

    def model(self, name: str) -> Annotation:
        return Annotation(
            name,
            decode=f'{reader(self.readers, name)}({{}})',
            models=frozenset({name}),
            readers=frozenset({name}),
        )

    def array(self, shape: Shape, item: Annotation) -> Annotation:
        del shape
        spelling = f'list[{item.spelling}]'
        if item.transparent:
            return Annotation(spelling, imports=item.imports, models=item.models, runtime=item.runtime)
        # `each` over a generator rather than a comprehension: the check runs
        # for what it reports, and a list of its results would be thrown away.
        # `fill`, not `format`: a discriminated check names its subject more
        # than once, and `str.format` counts those as separate placeholders.
        return Annotation(
            spelling,
            decode=f'each({fill(item.decode, "_item")} for _item in as_list({{}}))',
            imports=item.imports,
            models=item.models,
            runtime=item.runtime | CHECKS,
            readers=item.readers,
        )

    def union(self, shape: Shape) -> Annotation:
        """Spell a union as `A | B`, with a check that picks the member.

        Only the value is available, so members are told apart by what they are
        in JSON and, among objects, by a `discriminator:` or a required property
        no other member requires. The document states both of those.
        """
        members = self.members(shape)
        if not members:
            return _ANY

        # The spelling is what the document says, always. Widening it because
        # the *check* cannot tell two members apart would throw away what the
        # author wrote in order to describe a limitation of this generator.
        spelling = ' | '.join(dict.fromkeys(member.annotation.spelling for member in members))
        imports = frozenset[str]().union(*(member.annotation.imports for member in members))
        models = frozenset[str]().union(*(member.annotation.models for member in members))
        runtime = frozenset[str]().union(*(member.annotation.runtime for member in members))

        if all(member.annotation.transparent for member in members):
            return Annotation(spelling, imports=imports, models=models, runtime=runtime)
        decode, checked = _discriminated(members)
        # A member the document does not tell apart is never checked, so what
        # its check would call is not imported.
        return Annotation(
            spelling,
            decode=decode,
            imports=imports,
            models=models,
            runtime=(runtime - CHECKS).union(*(member.annotation.runtime for member in checked)),
            readers=frozenset[str]().union(*(member.annotation.readers for member in checked)),
        )


def reader(readers: Names, name: str) -> str:
    """The function that checks one `name`, as the model's module defines it."""
    return readers.claim(name, f'read_{module_name(name)}')


def make_annotator(tree: Tree, names: Names, readers: Names | None = None) -> PythonAnnotator:
    return PythonAnnotator(tree=tree, names=names, readers=readers if readers is not None else Names())


def _discriminated(members: list[Member]) -> tuple[str, list[Member]]:
    """Build one expression that checks whichever member arrived.

    Buckets come first, since a `list` and a `dict` are never each other.
    Objects are then told apart by a required property no sibling requires:
    `isbn` means a `Book` and `rating` means a `Review`. The document states
    both of those.

    The last arm is the `else` and carries no test. Where a bucket holds members
    that nothing in the document tells apart, the value is left unchecked:
    checking it as the wrong member would report properties it was never meant
    to carry. The members that *are* checked come back beside the expression.
    """
    by_bucket: dict[str, list[Member]] = {}
    for member in members:
        by_bucket.setdefault(member.bucket, []).append(member)

    arms: list[tuple[str | None, str]] = []
    checked: list[Member] = []
    for bucket in ('list', 'dict', 'scalar'):
        found = by_bucket.get(bucket)
        if not found:
            continue
        test = f'isinstance({{}}, {bucket})' if bucket in {'list', 'dict'} else None
        form, used = _within(found)
        arms.append((test, form))
        checked += used

    *tested, (_, fallback) = arms
    expression = fallback
    for test, form in reversed(tested):
        expression = f'({form} if {test} else {expression})'
    return expression, checked


def _within(members: list[Member]) -> tuple[str, list[Member]]:
    """Tell one bucket's members apart, using what the document says of them.

    A `discriminator:` comes first, since that is the author stating how to
    recognise one. Then a required property no sibling requires.

    Whatever is left over becomes the `else`. If more than one member is left,
    the document does not distinguish them and neither does this: the value is
    left unchecked, rather than checked as a `Book` when it is really a
    `Review`.
    """
    forms = {member.annotation.decode for member in members}
    if len(forms) == 1:
        return members[0].annotation.decode, members[:1]

    tested: list[tuple[str, str]] = []
    checked: list[Member] = []
    remaining: list[Member] = []
    for member in members:
        test = _test_for(member, members)
        if test is None:
            remaining.append(member)
        else:
            tested.append((test, member.annotation.decode))
            checked.append(member)

    if not remaining and tested:
        # Every member is distinguishable, so the last test is redundant: if the
        # value is none of the others it is this one.
        last = tested.pop()
        fallback = last[1]
    elif len(remaining) == 1:
        fallback = remaining[0].annotation.decode
        checked.append(remaining[0])
    else:
        fallback = IDENTITY

    expression = fallback
    for test, form in reversed(tested):
        expression = f'({form} if {test} else {expression})'
    return expression, checked


def _test_for(member: Member, members: list[Member]) -> str | None:
    """Return the test that recognises one member, or nothing if there is none."""
    if member.discriminator and member.discriminator_value is not None:
        return f'{{}}.get({member.discriminator!r}) == {member.discriminator_value!r}'
    others = frozenset[str]().union(*(one.required for one in members if one is not member))
    distinguishing = sorted(member.required - others)
    return f'{distinguishing[0]!r} in {{}}' if distinguishing else None

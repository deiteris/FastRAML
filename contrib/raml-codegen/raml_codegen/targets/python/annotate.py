"""The `python` target's spellings: dataclasses, and the code that converts them.

The traversal is in `targets/shared/annotate.py`. What is here is the six hooks
and the one thing only this target has to solve — telling a union's members
apart on the way in, with nothing but the value and what the document said.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..shared.annotate import IDENTITY, Annotation, Annotator, fill

if TYPE_CHECKING:
    from ...naming import Names
    from ...reader import Tree
    from ...tree import Shape
    from ..shared.annotate import Member

__all__ = ['PythonAnnotator']

#: The scalar kinds, and what each one is in Python. A bound, a pattern or a
#: `multipleOf` is a constraint rather than a type, so it reaches the docstring
#: and not the annotation. Exact decimals stay strings and are never divided
#: here: the parser takes care to keep a number out of a float, and a client is
#: no place to undo that.
_SCALARS: dict[str, Annotation] = {
    'any': Annotation('Any', imports=frozenset({'Any'})),
    'nil': Annotation('None'),
    'null': Annotation('None'),
    'boolean': Annotation('bool'),
    'string': Annotation('str'),
    'integer': Annotation('int'),
    'number': Annotation('float'),
    'file': Annotation('File', runtime=frozenset({'File'})),
    'datetime': Annotation(
        'datetime.datetime',
        encode='{}.isoformat()',
        decode='datetime.datetime.fromisoformat({})',
        imports=frozenset({'datetime'}),
    ),
    'datetime-only': Annotation(
        'datetime.datetime',
        encode='{}.isoformat()',
        decode='datetime.datetime.fromisoformat({})',
        imports=frozenset({'datetime'}),
    ),
    'date-only': Annotation(
        'datetime.date',
        encode='{}.isoformat()',
        decode='datetime.date.fromisoformat({})',
        imports=frozenset({'datetime'}),
    ),
    'time-only': Annotation(
        'datetime.time',
        encode='{}.isoformat()',
        decode='datetime.time.fromisoformat({})',
        imports=frozenset({'datetime'}),
    ),
}

_ANY = Annotation('Any', imports=frozenset({'Any'}))
_MAPPING = Annotation('dict[str, Any]', imports=frozenset({'Any'}))


class PythonAnnotator(Annotator):
    """Shapes as stdlib dataclasses, with `to_dict`/`from_dict` beside them."""

    __slots__ = ()

    def scalar(self, kind: str, shape: Shape | None = None) -> Annotation:
        # A bound, a pattern or a `multipleOf` is documentation here, so nothing
        # this target writes reads the shape.
        del shape
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
            encode='{}.to_dict()',
            decode=f'{name}.from_dict({{}})',
            models=frozenset({name}),
        )

    def array(self, shape: Shape, item: Annotation) -> Annotation:
        del shape
        # `fill`, not `format`: a discriminated decode names its subject more
        # than once, and `str.format` counts those as separate placeholders.
        encode = IDENTITY if item.transparent else f'[{fill(item.encode, "_item")} for _item in {{}}]'
        decode = IDENTITY if item.transparent else f'[{fill(item.decode, "_item")} for _item in as_list({{}})]'
        return Annotation(
            f'list[{item.spelling}]',
            encode=encode,
            decode=decode,
            imports=item.imports,
            models=item.models,
            runtime=item.runtime if item.transparent else item.runtime | {'as_list'},
        )

    def union(self, shape: Shape) -> Annotation:
        """Spell a union as `A | B`, with a converter that picks the member.

        A union is the one place a field does not know its own type until it
        holds a value, so identity conversion is wrong in both directions:
        `json=` would be handed a dataclass, and a parsed response would be a
        `dict` where the annotation promised a model.

        Going out, `to_json` looks at the value. Coming in, only the value's
        shape is available, so members are told apart by what they are in JSON
        and, among objects, by a required property no other member requires.
        The document states both of those.
        """
        members = self.members(shape)
        if not members:
            return _ANY

        # The spelling is what the document says, always. Widening it because
        # the *decode* cannot tell two members apart would throw away what the
        # author wrote in order to describe a limitation of this generator.
        spelling = ' | '.join(dict.fromkeys(member.annotation.spelling for member in members))
        imports = frozenset[str]().union(*(member.annotation.imports for member in members))
        models = frozenset[str]().union(*(member.annotation.models for member in members))
        runtime = frozenset[str]().union(*(member.annotation.runtime for member in members))

        if all(member.annotation.transparent for member in members):
            return Annotation(spelling, imports=imports, models=models, runtime=runtime)
        return Annotation(
            spelling,
            encode='to_json({})',
            decode=_discriminated(members),
            imports=imports,
            models=models,
            runtime=runtime | {'to_json'},
        )


def make_annotator(tree: Tree, names: Names) -> Annotator:
    return PythonAnnotator(tree=tree, names=names)


def _discriminated(members: list[Member]) -> str:
    """Build one expression that decodes whichever member arrived.

    Buckets come first, since a `list` and a `dict` are never each other.
    Objects are then told apart by a required property no sibling requires:
    `isbn` means a `Book` and `rating` means a `Review`. The document states
    both of those.

    The last arm is the `else` and carries no test. Where a bucket holds members
    that nothing in the document tells apart, the value is handed back as it
    arrived. A wrong member would be worse than an undecoded one.
    """
    by_bucket: dict[str, list[Member]] = {}
    for member in members:
        by_bucket.setdefault(member.bucket, []).append(member)

    arms: list[tuple[str | None, str]] = []
    for bucket in ('list', 'dict', 'scalar'):
        found = by_bucket.get(bucket)
        if not found:
            continue
        test = f'isinstance({{}}, {bucket})' if bucket in {'list', 'dict'} else None
        arms.append((test, _within(found)))

    *tested, (_, fallback) = arms
    expression = fallback
    for test, form in reversed(tested):
        expression = f'({form} if {test} else {expression})'
    return expression


def _within(members: list[Member]) -> str:
    """Tell one bucket's members apart, using what the document says of them.

    A `discriminator:` comes first, since that is the author stating how to
    recognise one. Then a required property no sibling requires.

    Whatever is left over becomes the `else`. If more than one member is left,
    the document does not distinguish them and neither does this: the value is
    handed back as it arrived, rather than as a `Book` that is really a
    `Review`.
    """
    forms = {member.annotation.decode for member in members}
    if len(forms) == 1:
        return members[0].annotation.decode

    tested: list[tuple[str, str]] = []
    remaining: list[Member] = []
    for member in members:
        test = _test_for(member, members)
        if test is None:
            remaining.append(member)
        else:
            tested.append((test, member.annotation.decode))

    if not remaining and tested:
        # Every member is distinguishable, so the last test is redundant: if the
        # value is none of the others it is this one.
        last = tested.pop()
        fallback = last[1]
    elif len(remaining) == 1:
        fallback = remaining[0].annotation.decode
    else:
        fallback = IDENTITY

    expression = fallback
    for test, form in reversed(tested):
        expression = f'({form} if {test} else {expression})'
    return expression


def _test_for(member: Member, members: list[Member]) -> str | None:
    """Return the test that recognises one member, or nothing if there is none."""
    if member.discriminator and member.discriminator_value is not None:
        return f'{{}}.get({member.discriminator!r}) == {member.discriminator_value!r}'
    others = frozenset[str]().union(*(one.required for one in members if one is not member))
    distinguishing = sorted(member.required - others)
    return f'{distinguishing[0]!r} in {{}}' if distinguishing else None

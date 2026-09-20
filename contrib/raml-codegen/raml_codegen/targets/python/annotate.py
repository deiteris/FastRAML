"""Turn one shape into a Python type.

An `Annotation` carries three things, and a template cannot work out any of them
from the others: how to spell the type, how to write one of its values as JSON,
and how to read one back. A `datetime` is `datetime.datetime`, `.isoformat()`
and `datetime.datetime.fromisoformat(...)`. A model is its class, `.to_dict()`
and `Cls.from_dict(...)`. Every other shape composes those.

The two conversions are code templates over one placeholder, `{}`, rather than
functions. Generated code has to read as code, and a chain of runtime converters
would put this package inside the client it generates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from ...naming import class_name, from_address
from ...reader import is_recursion, is_ref, items_of, members_of, properties_of

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ...naming import Names
    from ...reader import Tree
    from ...tree import Shape, ShapeNode

__all__ = ['Annotation', 'Annotator', 'fill']

#: `{}` is the value being converted. A form of `'{}'` is the identity, which is
#: what a JSON scalar needs and what most shapes are.
IDENTITY = '{}'


def fill(form: str, value: str) -> str:
    """Fill a conversion form's `{}` with the value being converted.

    Uses `replace` rather than `str.format`: a discriminated union names its
    subject once per arm, and `format` reads those as separate placeholders.
    """
    return form.replace(IDENTITY, value)


@dataclass(frozen=True, slots=True)
class Annotation:
    """One Python type, with the code that crosses the JSON boundary."""

    #: The annotation as it is written in generated source, e.g. `list[Book]`.
    spelling: str
    #: `{}` -> a JSON value.
    encode: str = IDENTITY
    #: a JSON value -> `{}`.
    decode: str = IDENTITY
    #: Names the generated file must import, such as `datetime` or `Literal`.
    imports: frozenset[str] = frozenset()
    #: Generated model classes this annotation names.
    models: frozenset[str] = frozenset()
    #: Names it needs from the generated package's own `types` module.
    runtime: frozenset[str] = frozenset()

    @property
    def transparent(self) -> bool:
        """True when JSON and Python are the same value, so no code is needed."""
        return self.encode == IDENTITY and self.decode == IDENTITY


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


@dataclass(slots=True)
class Annotator:
    """Turn shapes into annotations, and record which models were needed.

    Each model is declared once per address and named thereafter. The caller
    gets that set back, so nothing has to walk the tree again to find out which
    models to generate.
    """

    tree: Tree
    names: Names
    #: address -> the object shape a model is generated from, in the order the
    #: annotator first reached them.
    wanted: dict[str, Shape] = field(default_factory=dict)

    def of(self, node: ShapeNode | None, prefer: str | None = None) -> Annotation:
        """Return the Python type of one node.

        Follows a link, and stops at a recursion marker.

        `prefer` names a shape that has no usable name of its own. In the tree a
        response body is named after its media type, so `name` is
        `application/json`. That says how the value was sent and nothing about
        what it is, so the caller that knows the operation supplies a name.
        """
        if node is None:
            return _ANY
        if is_recursion(node):
            return self._recursion(node['head']['$ref'])
        if is_ref(node):
            address = node['$ref']
            target = self.tree.at(address)
            return self._of_shape(target, address, prefer) if target is not None else _ANY
        # The two guards above are `TypeGuard`s, which narrow only where they
        # are true; what is left here is a `Shape`.
        shape = cast('Shape', node)
        return self._of_shape(shape, shape.get('id'), prefer)

    # -- per kind ---------------------------------------------------------------

    def _of_shape(self, shape: Shape, address: str | None, prefer: str | None = None) -> Annotation:
        # A `json` shape is how a type *arrived*, not what it is: the spec
        # forbids it from participating in inheritance, so the parser decodes no
        # RAML facet from it and reading it directly reports a type made of
        # nothing (docs/16 § 11.10).
        content = self.tree.content_of(shape)
        kind = content['type']

        if content.get('enum') and kind in {'string', 'integer', 'number', 'boolean'}:
            return self._enum(content)
        if kind == 'object':
            return self._object(content, address or content.get('id'), prefer)
        if kind == 'array':
            return self._array(content, prefer)
        if kind == 'union':
            return self._union(content)
        return _SCALARS.get(kind, _ANY)

    def _enum(self, shape: Shape) -> Annotation:
        """Spell a closed set of values as a `Literal` rather than a class.

        RAML's `enum:` lists values and attaches no names to them. An `Enum`
        subclass would have to invent the names, and an identifier for
        `9780441013593` is guesswork a reader cannot check.
        """
        members = ', '.join(repr(value) for value in shape.get('enum', ()))
        return Annotation(f'Literal[{members}]', imports=frozenset({'Literal'}))

    def _object(self, shape: Shape, address: str | None, prefer: str | None = None) -> Annotation:
        inherited = self._is_its_supertype(shape)
        if inherited is not None:
            return inherited
        if not properties_of(shape):
            # Nothing named, so nothing to generate: an open object is a mapping
            # and `additionalProperties` says what may go in it.
            return _MAPPING
        if address is None:
            # An object with properties and no address cannot be referred to
            # twice, so it has no identity to hang a class on.
            return _MAPPING
        name = self._model(address, shape, prefer=prefer)
        return Annotation(
            name,
            encode='{}.to_dict()',
            decode=f'{name}.from_dict({{}})',
            models=frozenset({name}),
        )

    def _is_its_supertype(self, shape: Shape) -> Annotation | None:
        """Return the supertype where a shape is only a copy of it.

        The effective view inlines a supertype's properties wherever the
        supertype is not referenced by name (docs/16 § 11.3). So `body: Book`
        arrives as an anonymous object that carries every one of Book's
        properties and inherits `{"$ref": Book}`. Read literally, that is a
        distinct type, and generating it produces `PostBooksBody`: a duplicate
        of `Book` under a name the author never wrote, once per operation that
        mentions the type.

        A shape is the same type when it inherits exactly one declaration and
        names exactly that declaration's properties. Narrowing a facet does not
        break the match, because a tighter `maxLength` is still a `str` and a
        spelling is all this produces.

        Applies only to a shape with no name of its own, or with a media type in
        place of one. A named declaration is a type the author asked for, even
        where it restates its parent.
        """
        declared = shape.get('name')
        if declared and '/' not in declared:
            return None
        inherits = shape.get('inherits', [])
        if len(inherits) != 1:
            return None
        only = inherits[0]
        if not is_ref(only):
            return None
        target = self.tree.at(only['$ref'])
        if target is None:
            return None
        content = self.tree.content_of(target)
        if content['type'] != 'object' or set(properties_of(shape)) != set(properties_of(content)):
            return None
        return self._of_shape(content, only['$ref'])

    def _array(self, shape: Shape, prefer: str | None = None) -> Annotation:
        item = self.of(items_of(shape), _item_name(shape, prefer))
        # `replace`, not `format`: a discriminated decode names its subject more
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

    def _union(self, shape: Shape) -> Annotation:
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
        members = [self._member(node) for node in members_of(shape)]
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

    def _member(self, node: ShapeNode | None) -> _Member:
        resolved = self.tree.resolve(node)
        annotation = self.of(node)
        if resolved is None or is_recursion(resolved):
            return _Member('scalar', annotation, frozenset())
        content = self.tree.content_of(cast('Shape', resolved))
        kind = content['type']
        if kind == 'array':
            return _Member('list', annotation, frozenset())
        if kind != 'object':
            return _Member('scalar', annotation, frozenset())
        required = frozenset(name for name, prop in properties_of(content).items() if prop['required'])
        # `discriminator:`/`discriminatorValue:` is the document's own answer to
        # "which member is this", so it is asked first. Only where the tree
        # *states* a value: RAML defaults an unstated one to the type name, and
        # applying that default here would be this package holding a rule of the
        # language (docs/17 § 2).
        marker = content.get('discriminator') if content['type'] == 'object' else None
        value = content.get('discriminator_value') if content['type'] == 'object' else None
        return _Member('dict', annotation, required, marker, value)

    def _bucket(self, node: ShapeNode | None) -> str:
        """Say what a member is once it is JSON: a list, an object, or neither."""
        resolved = self.tree.resolve(node)
        if resolved is None or is_recursion(resolved):
            return 'scalar'
        kind = self.tree.content_of(cast('Shape', resolved))['type']
        if kind == 'array':
            return 'list'
        return 'dict' if kind == 'object' else 'scalar'

    def _recursion(self, head: str) -> Annotation:
        """Name the type that a recursion marker repeats.

        The annotation needs no quotes. Every generated module opens with
        `from __future__ import annotations`, so naming a class before it is
        bound costs nothing. What the marker provides is finiteness: without it
        a walk cannot tell a repeat from a fresh subtree.

        The decode form does evaluate the name, but inside a method body, by
        which time the class exists.
        """
        target = self.tree.at(head)
        if target is None:
            return _ANY
        model = self._model(head, target)
        return Annotation(
            model,
            encode='{}.to_dict()',
            decode=f'{model}.from_dict({{}})',
            models=frozenset({model}),
        )

    def pending(self) -> Iterator[tuple[str, Shape]]:
        """Models reached so far, in the order they were first reached."""
        return iter(tuple(self.wanted.items()))

    def _model(self, address: str, shape: Shape, *, prefer: str | None = None) -> str:
        """Claim the class name for one address.

        Declarations are claimed before any of this runs, in declaration order,
        so what reaches here is always an anonymous shape.

        `display_name` comes first because it is the only one of the three that
        an author wrote as a label. The structure calls a nested `items:` block
        `items`; its author calls it `Shelf slot`.
        """
        self.wanted.setdefault(address, shape)
        labelled = shape.get('display_name')
        structural = shape.get('name')
        for candidate in (labelled, prefer, structural):
            # A name with a slash in it is a media type: it says how the value
            # was sent, not what it is.
            if candidate and '/' not in candidate:
                return self.names.claim(address, class_name(candidate))
        return self.names.claim(address, from_address(address))


@dataclass(frozen=True, slots=True)
class _Member:
    """One member of a union, and what tells it apart from the others."""

    #: What it is once decoded: a `list`, a `dict`, or neither.
    bucket: str
    annotation: Annotation
    #: The properties it requires. Empty unless it is an object.
    required: frozenset[str]
    #: The property a discriminated hierarchy switches on, and this member's
    #: value for it. Both or neither.
    discriminator: str | None = None
    discriminator_value: object = None


def _item_name(shape: Shape, prefer: str | None) -> str | None:
    """Name an array's items where they are declared inline.

    Otherwise they are called `items`, which is the structure's word for the
    position rather than anybody's word for the type. Every inline array in the
    document wants that name, so the second one becomes `Items2`. The array is
    the only thing in scope that has a name, so the items borrow it.
    """
    for candidate in (prefer, shape.get('display_name'), shape.get('name')):
        # A name with a slash in it is a media type, which names the array no
        # better than it would name the items.
        if candidate and '/' not in candidate:
            return f'{candidate}-item'
    return None


def _discriminated(members: list[_Member]) -> str:
    """Build one expression that decodes whichever member arrived.

    Buckets come first, since a `list` and a `dict` are never each other.
    Objects are then told apart by a required property no sibling requires:
    `isbn` means a `Book` and `rating` means a `Review`. The document states
    both of those.

    The last arm is the `else` and carries no test. Where a bucket holds members
    that nothing in the document tells apart, the value is handed back as it
    arrived. A wrong member would be worse than an undecoded one.
    """
    by_bucket: dict[str, list[_Member]] = {}
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


def _within(members: list[_Member]) -> str:
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
    remaining: list[_Member] = []
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


def _test_for(member: _Member, members: list[_Member]) -> str | None:
    """Return the test that recognises one member, or nothing if there is none."""
    if member.discriminator and member.discriminator_value is not None:
        return f'{{}}.get({member.discriminator!r}) == {member.discriminator_value!r}'
    others = frozenset[str]().union(*(one.required for one in members if one is not member))
    distinguishing = sorted(member.required - others)
    return f'{distinguishing[0]!r} in {{}}' if distinguishing else None

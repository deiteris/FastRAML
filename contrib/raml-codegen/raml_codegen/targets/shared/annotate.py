"""Turn one shape into a type, in whichever language the target writes.

The **traversal** is here and the **spellings** are not. Descending a shape,
following a link, stopping at a recursion marker, deciding that an anonymous
body is really a declared type, claiming a name for one that is not — none of
that changes when the output language does, and each of them was a finding
rather than a reading. A target supplies the six hooks at the bottom and gets
all of it.

An `Annotation` carries three things, and a template cannot work out any of them
from the others: how to spell the type, how to write one of its values as JSON,
and how to read one back. A target whose runtime converts for it — pydantic does
— leaves the last two alone, and they stay the identity.

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

__all__ = ['IDENTITY', 'Annotation', 'Annotator', 'Member', 'fill']

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
    """One type, with the code that crosses the JSON boundary."""

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
    #: Names it needs from the generated package's own runtime module.
    runtime: frozenset[str] = frozenset()
    #: The spelling with nothing wrapped round it, where a target wraps one in a
    #: constraint. Empty when there is no difference, which is every annotation
    #: a target that only documents its facets produces.
    bare: str = ''

    @property
    def plain(self) -> str:
        r"""The type as a reader would write it, for a docstring or an `Args:`.

        `Annotated[str, Field(pattern=r'^\d{13}$')]` is what the route needs
        and `str` is what the line documenting it should say.
        """
        return self.bare or self.spelling

    @property
    def transparent(self) -> bool:
        """True when JSON and Python are the same value, so no code is needed."""
        return self.encode == IDENTITY and self.decode == IDENTITY


@dataclass(frozen=True, slots=True)
class Member:
    """One member of a union, and everything the document says to tell it apart."""

    #: What it is once decoded: a `list`, a `dict`, or neither.
    bucket: str
    annotation: Annotation
    #: The properties it requires. Empty unless it is an object.
    required: frozenset[str]
    #: The property a discriminated hierarchy switches on, and this member's
    #: value for it. Both or neither.
    discriminator: str | None = None
    discriminator_value: object = None


@dataclass(slots=True)
class Annotator:
    """Turn shapes into annotations, and record which models were needed.

    Each model is declared once per address and named thereafter. The caller
    gets that set back, so nothing has to walk the tree again to find out which
    models to generate.

    Subclass it and answer the six hooks below. Nothing else is meant to be
    overridden; what is above them is the tree, not the language.
    """

    tree: Tree
    names: Names
    #: address -> the object shape a model is generated from, in the order the
    #: annotator first reached them.
    wanted: dict[str, Shape] = field(default_factory=dict)

    # -- the spellings, which are each target's own ----------------------------

    def scalar(self, kind: str, shape: Shape | None = None) -> Annotation:
        """Spell one of the scalar kinds, or `any` for one this does not know.

        `shape` is absent only where there is no shape to read: a node that was
        `None`, or a link to an address the tree does not hold. A target that
        turns a facet into a constraint reads it from here.
        """
        raise NotImplementedError

    def enum(self, shape: Shape) -> Annotation:
        """Spell a closed set of values."""
        raise NotImplementedError

    def mapping(self) -> Annotation:
        """Spell an object that names no properties, so has no class to make."""
        raise NotImplementedError

    def model(self, name: str) -> Annotation:
        """Spell a reference to a generated model class, by the name it claimed."""
        raise NotImplementedError

    def array(self, shape: Shape, item: Annotation) -> Annotation:
        """Spell a list of `item`. `shape` carries `minItems` and its siblings."""
        raise NotImplementedError

    def union(self, shape: Shape) -> Annotation:
        """Spell a union. Read its members through `members`."""
        raise NotImplementedError

    # -- the traversal, which is not -------------------------------------------

    def of(self, node: ShapeNode | None, prefer: str | None = None) -> Annotation:
        """Return the type of one node.

        Follows a link, and stops at a recursion marker.

        `prefer` names a shape that has no usable name of its own. In the tree a
        response body is named after its media type, so `name` is
        `application/json`. That says how the value was sent and nothing about
        what it is, so the caller that knows the operation supplies a name.
        """
        if node is None:
            return self.scalar('any')
        if is_recursion(node):
            return self._recursion(node['head']['$ref'])
        if is_ref(node):
            address = node['$ref']
            target = self.tree.at(address)
            return self._of_shape(target, address, prefer) if target is not None else self.scalar('any')
        # The two guards above are `TypeGuard`s, which narrow only where they
        # are true; what is left here is a `Shape`.
        shape = cast('Shape', node)
        return self._of_shape(shape, shape.get('id'), prefer)

    def members(self, shape: Shape) -> list[Member]:
        """A union's members, each with what the document says of it."""
        return [self._member(node) for node in members_of(shape)]

    def pending(self) -> Iterator[tuple[str, Shape]]:
        """Models reached so far, in the order they were first reached."""
        return iter(tuple(self.wanted.items()))

    # -- per kind ---------------------------------------------------------------

    def _of_shape(self, shape: Shape, address: str | None, prefer: str | None = None) -> Annotation:
        # A `json` shape is how a type *arrived*, not what it is: the spec
        # forbids it from participating in inheritance, so the parser decodes no
        # RAML facet from it and reading it directly reports a type made of
        # nothing (docs/16 § 11.10).
        content = self.tree.content_of(shape)
        kind = content['type']

        if content.get('enum') and kind in {'string', 'integer', 'number', 'boolean'}:
            return self.enum(content)
        if kind == 'object':
            return self._object(content, address or content.get('id'), prefer)
        if kind == 'array':
            return self.array(content, self.of(items_of(content), _item_name(content, prefer)))
        if kind == 'union':
            return self.union(content)
        return self.scalar(kind, content)

    def _object(self, shape: Shape, address: str | None, prefer: str | None = None) -> Annotation:
        inherited = self._is_its_supertype(shape)
        if inherited is not None:
            return inherited
        if not properties_of(shape):
            # Nothing named, so nothing to generate: an open object is a mapping
            # and `additionalProperties` says what may go in it.
            return self.mapping()
        if address is None:
            # An object with properties and no address cannot be referred to
            # twice, so it has no identity to hang a class on.
            return self.mapping()
        return self.model(self._model(address, shape, prefer=prefer))

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

    def _member(self, node: ShapeNode | None) -> Member:
        resolved = self.tree.resolve(node)
        annotation = self.of(node)
        if resolved is None or is_recursion(resolved):
            return Member('scalar', annotation, frozenset())
        content = self.tree.content_of(cast('Shape', resolved))
        kind = content['type']
        if kind == 'array':
            return Member('list', annotation, frozenset())
        if kind != 'object':
            return Member('scalar', annotation, frozenset())
        required = frozenset(name for name, prop in properties_of(content).items() if prop['required'])
        # `discriminator:`/`discriminatorValue:` is the document's own answer to
        # "which member is this", so it is asked first. Only where the tree
        # *states* a value: RAML defaults an unstated one to the type name, and
        # applying that default here would be this package holding a rule of the
        # language (docs/17 § 2).
        #
        # Subscripted rather than taken from `kind`: the narrowing has to be on
        # `content` itself for the two `.get`s below to be the object variant's.
        marker = content.get('discriminator') if content['type'] == 'object' else None
        value = content.get('discriminator_value') if content['type'] == 'object' else None
        return Member('dict', annotation, required, marker, value)

    def _recursion(self, head: str) -> Annotation:
        """Name the type that a recursion marker repeats.

        The annotation needs no quotes. Every generated module opens with
        `from __future__ import annotations`, so naming a class before it is
        bound costs nothing. What the marker provides is finiteness: without it
        a walk cannot tell a repeat from a fresh subtree.
        """
        target = self.tree.at(head)
        if target is None:
            return self.scalar('any')
        return self.model(self._model(head, target))

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

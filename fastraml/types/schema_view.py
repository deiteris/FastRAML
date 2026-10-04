"""The JSON Schema projection's walk context, and the view shapes it builds.

docs/10-validation.md § 7. Shared by the projection (`schema_projection.py`)
and the `allOf` reducers (`schema_intersection.py`): the context each level of
the walk carries, a view `BaseShape` outside the parse's bookkeeping, the
diagnostic for a construct RAML cannot express, and the keywords that imply an
instance type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from fastraml.datanode import DataNode, value_node_of
from fastraml.errors import ErrorKind, RamlError
from fastraml.types.base import (
    TYPE_ARRAY,
    TYPE_NUMBER,
    TYPE_OBJECT,
    TYPE_STRING,
    BaseShape,
)
from fastraml.types.schema_compile import document_of, escape_json_pointer_segment, is_one_schema
from fastraml.uris import uri_stem

if TYPE_CHECKING:
    from referencing._core import Resolver

    from fastraml.types.base import Shape


@dataclass(frozen=True, slots=True)
class Projection:
    """What every level of the walk shares: where to hang the view shapes."""

    parent: BaseShape
    #: The compiled validator of the `JsonShape` the walk started at: its
    #: draft reads a document that names none.
    validator: Any
    resolver: Resolver[Any]
    defs: dict[str, BaseShape]
    #: JSON Pointer of the subschema being walked, within `resolver`'s document.
    pointer: str = ''
    #: A containing object/array can recover from an impossible child restriction.
    allow_empty: bool = False

    def at(self, resolver: Resolver[Any], pointer: str) -> Projection:
        """The same walk, moved into another document at `pointer`."""
        return Projection(self.parent, self.validator, resolver, self.defs, pointer, self.allow_empty)

    def into(self, *segments: str) -> Projection:
        """One step deeper in the current document."""
        suffix = ''.join(f'/{escape_json_pointer_segment(segment)}' for segment in segments)
        return Projection(
            self.parent, self.validator, self.resolver, self.defs, self.pointer + suffix, self.allow_empty
        )


type Visiting = dict[int, BaseShape | None]


def unsupported(context: Projection, what: str) -> RamlError:
    return RamlError.new(
        'JSON schema construct has no RAML equivalent',
        context.parent.location,
        context.parent.value_pos,
        kind=ErrorKind.RESOLVING,
        info={'construct': what},
    )


def _subschema_uri(context: Projection, document: str | None) -> str | None:
    """The canonical URI of the subschema being walked, or `None`.

    Document plus JSON Pointer, so `location` *is* the identity — one field, and
    a URI with a fragment, which `location` already carries for a RAML type
    declared from `schema.json#/definitions/User`.

    `None` where the document is not the schema: every schema written inline in
    an API or a library compiles under that one URI, so it identifies none of
    them. The absence of a `#` is then what tells a consumer to fall back to
    addressing by containment.
    """
    if not is_one_schema(context.parent._raml, document):  # noqa: SLF001 - the parse's index
        return None
    return f'{document}#{context.pointer}'


def view_base(context: Projection, name: str | None = None) -> BaseShape:
    """A `BaseShape` outside the parse's own bookkeeping.

    Deliberately not `put_shape`d and not `put_typedef`d: these are not
    declarations the document made, and P9 and P10 must never reach them.
    Marked unwrapped so a consumer serialising the model emits the concrete type
    inline rather than an inheritance link that leads nowhere.

    `location` is the **schema** document being walked, not the RAML file that
    reached it: it is where the type is, and it is the same answer however many
    RAML types reference it. `key_pos` stays unknown, so nothing that pairs the
    two prints a position.

    `name` likewise comes off that URI rather than off whichever reference
    arrived first. One shape is reached both as the RAML type that `!include`d
    the document and as another schema's `$ref`, and only the second carries a
    name -- so naming it from the caller left the same type named or nameless
    depending on walk order.
    """
    document = document_of(context.resolver)
    location = _subschema_uri(context, document)
    base = BaseShape(
        id=context.parent._raml.next_id(),  # noqa: SLF001 - one counter per parse (docs/02 § 3)
        raml=context.parent._raml,  # noqa: SLF001 - as above
        location=location or context.parent.location,
        name=name or (subschema_name(location) if location else None),
    )
    base._unwrapped = True  # noqa: SLF001 - a view shape has nothing left to flatten
    return base


def subschema_name(location: str) -> str | None:
    """What the subschema at `location` is called, from the URI that identifies it.

    A whole document is called after its file and a `definitions` or `$defs`
    entry after its key -- the two forms JSON Schema itself lets a `$ref`
    address. Anything else, `#/properties/items/items` say, is where a schema
    sits inside a document rather than a type anyone wrote down, and stays
    nameless so a consumer prints it where it stands.
    """
    document, _, pointer = location.partition('#')
    if not pointer:
        return uri_stem(document) or None
    parent, _, key = pointer.rpartition('/')
    return key.replace('~1', '/').replace('~0', '~') if parent in {'/definitions', '/$defs'} else None


#: JSON Schema keywords that apply to exactly one instance type.
#:
#: **Deliberately not RAML's `FACET_TYPE_HINT`**, though the two overlap. That
#: table is wrong here in both directions: it maps `fileTypes` and
#: `discriminator`, which are not JSON Schema keywords, and it omits
#: `patternProperties`, `required`, `dependencies`, `contains` and
#: `exclusiveMinimum`, which are. Its `identify_shape_type` also *raises* on a
#: declaration hinting at two kinds — and a JSON schema constraining two kinds at
#: once is legal and ordinary, so borrowing it would reject valid input.
KEYWORD_TYPE: Final[dict[str, str]] = {
    'properties': TYPE_OBJECT,
    'patternProperties': TYPE_OBJECT,
    'additionalProperties': TYPE_OBJECT,
    'propertyNames': TYPE_OBJECT,
    'dependencies': TYPE_OBJECT,
    'required': TYPE_OBJECT,
    'minProperties': TYPE_OBJECT,
    'maxProperties': TYPE_OBJECT,
    'items': TYPE_ARRAY,
    'additionalItems': TYPE_ARRAY,
    'contains': TYPE_ARRAY,
    'minItems': TYPE_ARRAY,
    'maxItems': TYPE_ARRAY,
    'uniqueItems': TYPE_ARRAY,
    'minLength': TYPE_STRING,
    'maxLength': TYPE_STRING,
    'pattern': TYPE_STRING,
    'minimum': TYPE_NUMBER,
    'maximum': TYPE_NUMBER,
    'exclusiveMinimum': TYPE_NUMBER,
    'exclusiveMaximum': TYPE_NUMBER,
    'multipleOf': TYPE_NUMBER,
}


def view_data(base: BaseShape, value: Any) -> DataNode:
    return DataNode(value=value_node_of(value), location=base.location)


def attach(base: BaseShape, kind: str, shape: Shape) -> BaseShape:
    base.type = kind
    base.shape = shape
    return base


def attach_kind(base: BaseShape, kind: str, cls: Any, **built: Any) -> BaseShape:
    return attach(base, kind, cls(base, **built))

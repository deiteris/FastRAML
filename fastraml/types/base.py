"""The shape model's foundations.

A RAML type declaration is one `BaseShape` holding one kind-specific `Shape`
object. The split is load-bearing rather than tidy: the kind of a declaration is
unknown when the object is created (`type: Foo` cannot be classified until `Foo`
resolves), and it can change again during resolution, so the kind object has to
be replaceable without invalidating any reference already taken to the
`BaseShape`. See docs/05-type-model.md § 1.

`ScalarFacet` lives here too, because every facet of every shape is one, and a
class that `types/` holds cannot live under `parser/` without inverting the
layering.

Nothing here has a runtime dependency on `fastraml.parser`: those names appear in
annotations only, which `from __future__ import annotations` keeps as strings.
Building a `ScalarFacet` from YAML does need the parser — an include has to be
read, the annotated-scalar form unwrapped — so the builder stays in
`fastraml.parser.facets`. See docs/02-architecture.md § 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from fastraml.datanode import make_data_node
from fastraml.errors import Accumulator, ErrorKind, RamlError
from fastraml.positions import UNKNOWN, Position
from fastraml.types.values import ValueSet

if TYPE_CHECKING:
    import re
    from collections.abc import Iterator, Mapping

    from fastraml.datanode import DataNode
    from fastraml.domains import DomainLocation
    from fastraml.parser.annotations import DomainExtension
    from fastraml.parser.fragments import DataTypeFragment, LibraryLink, ReferenceResolver
    from fastraml.parser.includes import IncludeInfo
    from fastraml.registry import Raml
    from fastraml.types.examples import Example, Examples
    from fastraml.types.xml import XmlSerialization
    from fastraml.yamlnode import Node

__all__ = [
    'BUILTIN_TYPES',
    'ONE_SHAPE',
    'PROPERTIES',
    'SHAPE_LIST',
    'TYPE_ANY',
    'TYPE_ARRAY',
    'TYPE_BOOLEAN',
    'TYPE_COMPOSITE',
    'TYPE_DATETIME',
    'TYPE_DATETIME_ONLY',
    'TYPE_DATE_ONLY',
    'TYPE_FILE',
    'TYPE_INTEGER',
    'TYPE_JSON',
    'TYPE_NIL',
    'TYPE_NULL',
    'TYPE_NUMBER',
    'TYPE_OBJECT',
    'TYPE_RECURSIVE',
    'TYPE_STRING',
    'TYPE_TIME_ONLY',
    'TYPE_UNION',
    'BaseShape',
    'Binding',
    'DeclarationFacet',
    'KindBase',
    'Parameter',
    'PatternProperty',
    'Property',
    'ScalarFacet',
    'Shape',
    'TypeExprRef',
    'declaration_facets',
]

# The built-in type names (spec section Raml Data Types). `null` is the spec's
# alias for `nil`.
TYPE_ANY: Final = 'any'
TYPE_STRING: Final = 'string'
TYPE_INTEGER: Final = 'integer'
TYPE_NUMBER: Final = 'number'
TYPE_BOOLEAN: Final = 'boolean'
TYPE_DATETIME: Final = 'datetime'
TYPE_DATETIME_ONLY: Final = 'datetime-only'
TYPE_DATE_ONLY: Final = 'date-only'
TYPE_TIME_ONLY: Final = 'time-only'
TYPE_ARRAY: Final = 'array'
TYPE_OBJECT: Final = 'object'
TYPE_FILE: Final = 'file'
TYPE_NIL: Final = 'nil'
TYPE_NULL: Final = 'null'

# Kinds that exist in the model but are not names a document may use, except
# `union`, which a type expression produces and go-raml also accepts written out.
TYPE_UNION: Final = 'union'
TYPE_JSON: Final = 'json'
TYPE_COMPOSITE: Final = 'composite'
TYPE_RECURSIVE: Final = 'recursive'

#: A declaration may not take one of these as its name.
BUILTIN_TYPES: Final = frozenset(
    {
        TYPE_ANY,
        TYPE_STRING,
        TYPE_INTEGER,
        TYPE_NUMBER,
        TYPE_BOOLEAN,
        TYPE_DATETIME,
        TYPE_DATETIME_ONLY,
        TYPE_DATE_ONLY,
        TYPE_TIME_ONLY,
        TYPE_ARRAY,
        TYPE_OBJECT,
        TYPE_FILE,
        TYPE_NIL,
        TYPE_NULL,
        TYPE_UNION,
    }
)


@dataclass(slots=True, eq=False)
class ScalarFacet[T]:
    """One scalar-valued facet: its value, where it came from, its annotations.

    A facet is never a bare Python value, so `minLength must be <= maxLength`
    can point at the exact line that declared each. The companion rule
    (docs/05 § 1): a facet holding a single scalar is a `ScalarFacet[T]`,
    one holding arbitrary user data is a `DataNode`.
    """

    value: T
    location: str
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN
    #: Set when the value arrived through `!include`.
    include: IncludeInfo | None = None
    #: Annotations collected from the annotated-scalar form (docs/03 § 7).
    annotations: dict[str, DomainExtension] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f'ScalarFacet({self.value!r})'


@dataclass(frozen=True, slots=True, eq=False)
class TypeExprRef:
    """Where one name inside a type expression was written, and what it means.

    Emitted by P7 for every type name and built-in keyword in an expression,
    never for the `[]`, `?` or `|` operators, whose extent is implied by their
    operands. `lib.Type` produces two: one for the prefix, which navigates to
    the library file, and one for the name, which navigates to the declaration.

    Nothing in the parser reads these. They cost one small object per name and
    are what lets a future LSP offer go-to-definition and hover without
    re-lexing. See docs/06-type-expressions.md § 3.
    """

    #: 1-based, in the file that wrote the expression.
    line: int
    #: 1-based file column, already rebased off the expression's own column.
    column: int
    #: The declaration a type name refers to.
    resolved: BaseShape | None = None
    #: The `lib` half of `lib.Type`, and the alias exactly as written.
    library_link: LibraryLink | None = None
    library_alias: str | None = None
    #: The keyword, when the name was a primitive rather than a reference.
    builtin: str | None = None


class BaseShape:
    """The half of a declaration that every kind has in common.

    Created before the kind is known, so `shape` starts as `None` and is filled
    by `make_shape` in the same breath — with an `UnknownShape` when the kind
    cannot be settled yet. `id` is unique within one parse, and is what the
    clone operations key their memo on (docs/07 § 6).
    """

    __slots__ = (  # noqa: RUF023 - grouped by role
        'id',
        'name',
        'type',
        'shape',
        # common facets (spec section Type Declarations)
        'display_name',
        'description',
        'default',
        'required',
        'example',
        'examples',
        'enum',
        'xml',
        # An annotation-type facet, but stored on every base: which facets a
        # declaration may carry is decided by `shape.py`, not by this class.
        'allowed_targets',
        # structure
        'inherits',
        'alias',
        'link',
        'custom_facets',
        'custom_facet_defs',
        'annotations',
        # provenance / tooling
        'type_expr',
        'type_expr_refs',
        'is_annotation_type',
        'anchor',
        'location',
        'key_pos',
        'value_pos',
        # state
        '_unwrapped',
        '_visiting',
        '_raml',
        '_enum_index',
    )

    def __init__(  # noqa: PLR0913 - a model constructor names its fields
        self,
        *,
        id: int,  # noqa: A002 - every model entity names its id field `id`
        raml: Raml,
        location: str,
        name: str | None = None,
        key_pos: Position = UNKNOWN,
        value_pos: Position = UNKNOWN,
        anchor: ReferenceResolver | None = None,
        is_annotation_type: bool = False,
    ) -> None:
        self.id = id
        self.name = name
        #: The kind name once known: 'string', 'object', … Empty until then.
        self.type: str = ''
        #: The kind-specific half. Attached by `make_shape`; swapped by P7.
        self.shape: Shape | None = None

        self.display_name: ScalarFacet[str] | None = None
        self.description: ScalarFacet[str] | None = None
        self.default: DataNode | None = None
        self.required: ScalarFacet[bool] | None = None
        self.example: Example | None = None
        self.examples: Examples | None = None
        self.enum: list[DataNode] | None = None
        self.xml: XmlSerialization | None = None
        #: `allowedTargets:` on an annotation type. `None` and `[]` differ and
        #: the difference must survive to P10: absent means *any* target, empty
        #: means none at all (docs/09 § B4).
        self.allowed_targets: list[DomainLocation] | None = None

        # The containers are allocated eagerly: an empty dict costs less than a
        # `None` check at every read across four passes.
        self.inherits: list[BaseShape] = []
        self.alias: BaseShape | None = None
        self.link: DataTypeFragment | None = None
        self.custom_facets: dict[str, DataNode] = {}
        self.custom_facet_defs: dict[str, Property] = {}
        self.annotations: dict[str, DomainExtension] = {}

        #: The type expression exactly as written, so P7 can report a column
        #: inside it and tooling can offer go-to-definition on each name.
        self.type_expr: Node | None = None
        self.type_expr_refs: list[TypeExprRef] = []
        self.is_annotation_type = is_annotation_type
        #: The scope unqualified names in this declaration resolve in.
        self.anchor = anchor
        self.location = location
        self.key_pos = key_pos
        self.value_pos = value_pos

        # Owned by later passes: unwrap (P9) and the recursion marker.
        self._unwrapped = False
        self._visiting = False
        self._raml = raml
        #: `enum` as a `ValueSet`, and the list it was built from. Rebuilt when
        #: `enum` is rebound; nothing mutates the list in place (docs/07 § 4).
        self._enum_index: tuple[list[DataNode], ValueSet] | None = None

    def __repr__(self) -> str:
        return f'BaseShape(id={self.id}, name={self.name!r}, type={self.type!r})'

    # -- copying (docs/07-resolution-and-inheritance.md § 6) -------------------

    def clone(self, memo: dict[int, BaseShape]) -> BaseShape:
        """A deep, **structure-preserving** copy.

        `memo` is keyed on `BaseShape.id`, so a diamond stays a diamond and a
        cycle stays a cycle — this model has both, and a copy that turned a
        cycle into infinite recursion would be a hang rather than a bug report.
        Pass the same `memo` across calls to keep several shapes' shared parts
        shared; pass a fresh one, via `clone_detached`, to share nothing.

        The clone keeps the original's `id`. That is what lets `memo` be keyed
        on it, and a caller who needs a distinct identity — union member
        merging is the one that does — assigns a fresh one from
        `Raml.next_id()`.

        `copy.deepcopy` is not an option: it would copy the `Raml`
        back-pointer, the compiled `re.Pattern` objects and the YAML nodes.
        """
        existing = memo.get(self.id)
        if existing is not None:
            return existing

        clone = BaseShape(
            id=self.id,
            raml=self._raml,
            location=self.location,
            name=self.name,
            key_pos=self.key_pos,
            value_pos=self.value_pos,
            anchor=self.anchor,
            is_annotation_type=self.is_annotation_type,
        )
        # Registered before the children are copied, so a cycle back to this
        # shape finds the in-progress clone instead of recursing forever.
        memo[self.id] = clone

        clone.type = self.type
        # Facets are never mutated in place, only rebound, so they are shared.
        clone.display_name = self.display_name
        clone.description = self.description
        clone.default = self.default
        clone.required = self.required
        clone.example = self.example
        clone.examples = self.examples
        clone.enum = self.enum
        clone.xml = self.xml
        # Copied, not shared: `None` versus a list is meaningful here, and a
        # shared list would let one clone's narrowing reach the original.
        clone.allowed_targets = None if self.allowed_targets is None else list(self.allowed_targets)
        clone.type_expr = self.type_expr
        clone.type_expr_refs = list(self.type_expr_refs)
        clone._unwrapped = self._unwrapped

        # The containers are what unwrap mutates, so each gets its own.
        clone.custom_facets = dict(self.custom_facets)
        clone.annotations = dict(self.annotations)
        clone.custom_facet_defs = {
            name: prop.with_base(prop.base.clone(memo)) for name, prop in self.custom_facet_defs.items()
        }
        clone.inherits = [parent.clone(memo) for parent in self.inherits]
        clone.alias = self.alias.clone(memo) if self.alias is not None else None

        if self.link is not None and self.link.shape is not None:
            # A link is rewritten to inheritance at the start of unwrap
            # (docs/07 § 1), and unwrap is the only thing that reads one.
            # Doing it here rather than copying the fragment keeps a file to one
            # `DataTypeFragment` per parse, which invariant I3 depends on.
            clone.link = None
            clone.inherits = [self.link.shape.clone(memo)]
        else:
            clone.link = self.link

        clone.shape = self.shape.clone(clone, memo) if self.shape is not None else None
        return clone

    def clone_detached(self) -> BaseShape:
        """`clone` with a fresh memo: parents, links and aliases copied too.

        The result shares nothing with the original, so mutating it — which is
        what unwrap does — cannot reach the declared model. Costs a full copy
        per call, which is why the default is `clone` with a shared memo.
        """
        return self.clone({})

    # -- validation (docs/10-validation.md) -----------------------------------

    def check(self) -> None:
        """Is this declaration self-consistent? Raises; does not return a flag.

        The base-level half is `enum`: every member is validated against this
        shape, so `type: integer, enum: [1, "two"]` fails at the declaration
        rather than at first use. The kind's own facet rules follow.
        """
        if self.shape is None:
            raise RamlError.new('declaration has no shape', self.location, self.key_pos, kind=ErrorKind.VALIDATING)
        if not self.enum:
            # The common case, and once per nested declaration: with nothing to
            # accumulate beside the kind's own check, no accumulator is needed.
            self.shape.check()
            return
        accumulator = Accumulator()
        for index, member in enumerate(self.enum):
            try:
                # `self.shape.validate`, not `self.validate_at`: the latter
                # short-circuits on enum membership, which would make every
                # member trivially valid against the enum it belongs to.
                self.shape.validate(member.raw, f'enum[{index}]')
            except RamlError as err:
                accumulator.add(
                    RamlError.wrap(
                        'invalid enum member',
                        err,
                        member.location,
                        member.value_pos,
                        kind=ErrorKind.VALIDATING,
                        info={'index': index},
                    )
                )
        try:
            self.shape.check()
        except RamlError as err:
            accumulator.add(err)
        accumulator.raise_if_any()

    def validate_at(self, value: Any, path: str) -> None:
        """Does `value` conform? The internal entry point; raises on failure.

        **Enum first** (docs/10 § 3): when a shape has an `enum`,
        membership is the whole check, because `check()` already validated every
        member against the shape's facets.
        """
        if self.shape is None:
            raise RamlError.new('declaration has no shape', self.location, self.key_pos, kind=ErrorKind.VALIDATING)
        if self.enum:
            if value not in self._enum_members():
                raise RamlError.new(
                    'value is not one of the allowed values',
                    self.location,
                    self.value_pos,
                    kind=ErrorKind.VALIDATING,
                    info={'path': path, 'allowed': [member.raw for member in self.enum]},
                )
            return
        self.shape.validate(value, path)

    def _enum_members(self) -> ValueSet:
        """`enum` indexed by semantic equality, built on first use (docs/10 § 5)."""
        enum = self.enum or []
        cached = self._enum_index
        if cached is None or cached[0] is not enum:
            cached = self._enum_index = (enum, ValueSet(member.raw for member in enum))
        return cached[1]

    def _assert_unwrapped(self) -> None:
        """Invariant I12: data is validated against a *flattened* declaration.

        An un-flattened shape shows only what its own declaration wrote, so it
        silently answers a different question: a child whose parent declared a
        required property accepts a value that omits it (docs/13 § 3). P10
        unwraps a private copy for this reason, and a caller holding a shape from
        a `unwrap=False` parse has to do the same.

        A bug, not a diagnostic — hence `assert` (docs/02 § 4).

        At the public entry only. `validate_at` recurses through every node of a
        value and is the hot path; the invariant covers the whole subtree once it
        holds at the root.
        """
        assert self._unwrapped, (  # noqa: S101 - invariant I12 (docs/02 § 4), not input validation
            'validate() needs an unwrapped shape: parse with ParseOptions(unwrap=True), '
            'or call unwrap_shape() on a detached clone'
        )

    def validate(self, value: Any) -> RamlError | None:
        """The public data-validation entry point (docs/13 § 3).

        Returns the failure rather than raising it: the common use is a
        boolean-ish check in a request handler, where an exception is the wrong
        control flow. `validate_or_raise` is the other case.
        """
        self._assert_unwrapped()
        try:
            self.validate_at(value, '$')
        except RamlError as err:
            return err
        return None

    def validate_or_raise(self, value: Any) -> None:
        """`validate`, for callers who would only re-raise what it returns."""
        self._assert_unwrapped()
        self.validate_at(value, '$')


@dataclass(slots=True, eq=False)
class Property:
    """A named property, header, query parameter, URI parameter or facet
    declaration — all share one syntax (docs/05 § 4).
    """

    name: str
    base: BaseShape
    required: bool

    def __repr__(self) -> str:
        return f'Property({self.name!r}, required={self.required})'

    def with_base(self, base: BaseShape) -> Property:
        """The same property declaring `base`; itself when `base` already is its own.

        A `Property` is never mutated, only replaced, so an unchanged one is
        shared rather than rebuilt. Unwrap and recursion marking visit every
        property of every declaration, and most come back unchanged.
        """
        if base is self.base:
            return self
        return Property(name=self.name, base=base, required=self.required)


@dataclass(slots=True, eq=False)
class PatternProperty:
    """A `/regex/` key found inside `properties:`.

    Always optional by definition, and matched in declaration order — the first
    pattern that matches wins (docs/05 § 4).
    """

    pattern: re.Pattern[str]
    base: BaseShape

    def __repr__(self) -> str:
        return f'PatternProperty({self.pattern.pattern!r})'


#: Where a parameter is bound. `baseUriParameters` binds as `uri`: it declares
#: the same thing about the same template variables (docs/08 § 6.2).
type Binding = Literal['uri', 'query', 'header']


@dataclass(slots=True, eq=False)
class Parameter:
    """One *bound* parameter: a property declaration, plus where it is bound.

    A `Property` is a name, a shape and a flag, because one syntax declares an
    object's property, a header, a query parameter and a URI parameter alike.
    Which of those it is belongs to the *use* and not to the type — the same
    declared type is a required path parameter here and an optional header
    there — so a `Property` cannot say, and does not try to.

    That is also why it carries no id and no position: it is a record, and only
    entities get ids (docs/02 § 3). A bound parameter is an entity. It
    holds the property rather than restating it, so the optionality rules of
    `make_property` stay in one place, and it adds the two facts the property
    has nowhere to put — the binding, and where the key was written.
    """

    id: int
    binding: Binding
    #: Not named `property`: the annotation would shadow the builtin for the
    #: rest of the class body, and the delegates below need it.
    declaration: Property
    key_pos: Position = UNKNOWN
    value_pos: Position = UNKNOWN
    #: True only for an undeclared resource URI variable supplied by P6.
    synthesized: bool = False

    @property
    def name(self) -> str:
        return self.declaration.name

    @property
    def base(self) -> BaseShape:
        return self.declaration.base

    @property
    def required(self) -> bool:
        return self.declaration.required

    def __repr__(self) -> str:
        return f'Parameter({self.binding}, {self.declaration.name!r})'


@dataclass(frozen=True, slots=True)
class DeclarationFacet:
    """A facet whose value is one or more declarations rather than data.

    A kind that has any publishes them in a class-level `DECLARATION_FACETS`
    table. `make_shape` reads the table off the class it is about to construct,
    builds the children itself, and passes them in under `fields`. The kind
    never calls back into `shape.py`, so `shape.py` imports the kind modules
    and never the reverse.
    """

    #: How to read the facet's value node.
    kind: Literal['shape', 'shape_list', 'properties']
    #: The constructor keywords the built children arrive under. `properties:`
    #: fills two, because `/regex/` keys inside it are routed to pattern
    #: properties (docs/05 § 4).
    fields: tuple[str, ...]


#: The three declaration facets in the language, named once so that a kind's
#: table and `make_shape`'s dispatch cannot drift apart.
ONE_SHAPE = DeclarationFacet('shape', ('items',))
SHAPE_LIST = DeclarationFacet('shape_list', ('any_of',))
PROPERTIES = DeclarationFacet('properties', ('properties', 'pattern_properties'))

_NO_DECLARATION_FACETS: Mapping[str, DeclarationFacet] = {}


def declaration_facets(kind: type[Shape]) -> Mapping[str, DeclarationFacet]:
    """The declaration-holding facets of a kind; empty for the fourteen without.

    Read through a helper rather than declared on every `Shape`, so the kinds
    that hold no declarations carry nothing they do not use.
    """
    return getattr(kind, 'DECLARATION_FACETS', _NO_DECLARATION_FACETS)


_SLOT_CACHE: dict[type, tuple[str, ...]] = {}


def copyable_slots(kind: type) -> tuple[str, ...]:
    """Every field a kind holds, `base` excepted, nearest class first.

    `__slots__` is per class, so the whole MRO has to be walked; the result is
    cached because `clone` runs once per shape per validated type.
    """
    cached = _SLOT_CACHE.get(kind)
    if cached is None:
        names: list[str] = []
        for klass in kind.__mro__:
            names += [name for name in getattr(klass, '__slots__', ()) if name != 'base']
        cached = tuple(names)
        _SLOT_CACHE[kind] = cached
    return cached


_FACET_CACHE: dict[type, tuple[tuple[str, str], ...]] = {}


def facet_slots(kind: type) -> tuple[tuple[str, str], ...]:
    """Every constraint slot a kind declares, as `(slot, RAML spelling)`.

    Read off `__slots__` rather than from a per-kind table, so a facet added to
    a kind reaches every emitter without any emitter being edited. That is the
    same argument `copyable_slots` makes for `clone`, and it is load-bearing for
    the same reason: a hand-written list goes stale in silence, and a view that
    omits a constraint looks exactly like a type that does not have it.

    The RAML spelling is `lowerCamelCase` throughout — `multiple_of` is
    `multipleOf`, `unique_items` is `uniqueItems` — so no exception table is
    needed and none should be reintroduced without a name that needs one.
    """
    cached = _FACET_CACHE.get(kind)
    if cached is None:
        cached = tuple((slot, _camel(slot)) for slot in copyable_slots(kind))
        _FACET_CACHE[kind] = cached
    return cached


def facets_of(shape: object) -> Iterator[tuple[str, ScalarFacet]]:
    """Every constraint `shape` actually carries, in RAML spelling.

    The `isinstance` is the whole filter: a container facet — `properties`,
    `items`, `any_of` — is not a `ScalarFacet`, so naming those to exclude them
    is redundant and an exclusion list would only be able to go wrong.
    """
    for slot, spelling in facet_slots(type(shape)):
        value = getattr(shape, slot, None)
        if isinstance(value, ScalarFacet):
            yield spelling, value


def _camel(name: str) -> str:
    head, _, rest = name.partition('_')
    return head + ''.join(part.title() for part in rest.split('_') if part)


class KindBase:
    """What every kind object shares: the back-pointer, and the unwritten half.

    A shared base for the kind objects, not a facet hierarchy: a facet on this
    class would be one no `BaseShape` knows about.

    The default `decode_facets` files a key the kind does not recognise as a
    custom facet value (docs/05 § 3). Kinds with facets of their
    own handle those and pass the rest up.
    """

    __slots__ = ('base',)

    def __init__(self, base: BaseShape) -> None:
        self.base = base

    def decode_facets(self, pairs: list[Node]) -> None:
        for index in range(0, len(pairs), 2):
            key = pairs[index]
            self.base.custom_facets[key.value] = make_data_node(
                self.base._raml,  # noqa: SLF001 - the kind is the base's other half
                key,
                pairs[index + 1],
                self.base.location,
            )

    # Abstract: every kind supplies all three. They are declared here rather
    # than left to the `Shape` protocol so that a kind added later fails loudly
    # instead of silently accepting every value.

    def is_scalar(self) -> bool:
        raise NotImplementedError

    def check(self) -> None:
        raise NotImplementedError

    def validate(self, value: Any, path: str) -> None:
        raise NotImplementedError

    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> Shape:  # noqa: ARG002 - the four kinds that override this need `memo`
        """Copy this kind onto an already-cloned `base` (docs/07 § 6).

        Every facet a kind holds is a `ScalarFacet` or a list of them, and
        nothing ever mutates one in place — `inherit` only ever rebinds the
        field — so they are shared rather than copied. The three kinds that hold
        *declarations* override this and clone those through `memo`.

        Driven off `__slots__` rather than written out seventeen times. That is
        sound here only because `__slots__` on every model class is a project
        rule (AGENTS.md), so the field list cannot go stale.
        """
        clone = type(self)(base)
        for name in copyable_slots(type(self)):
            setattr(clone, name, getattr(self, name))
        return clone

    def __repr__(self) -> str:
        return f'{type(self).__name__}(id={self.base.id})'


class Shape(Protocol):
    """The kind-specific half of a declaration.

    `inherit` and `alias_to` are deliberately *not* here — they are functions
    over two `BaseShape`s in `types/inherit.py` (see its docstring). `check` and `validate` are, because they dispatch on kind
    and recurse into their own children rather than through a driver.
    """

    base: BaseShape

    def decode_facets(self, pairs: list[Node]) -> None:
        """Read the leftover facet list, flat as `[k0, v0, k1, v1, …]`.

        Declaration-valued facets have already been removed and handed to the
        constructor, so everything arriving here is data. Anything the kind does
        not recognise becomes a custom facet value on `base.custom_facets`.
        """
        ...

    def check(self) -> None:
        """Is the declaration self-consistent? (P10)"""
        ...

    def validate(self, value: Any, path: str) -> None:
        """Does a data value conform to this declaration? (P10)"""
        ...

    def clone(self, base: BaseShape, memo: dict[int, BaseShape]) -> Shape: ...

    def is_scalar(self) -> bool: ...

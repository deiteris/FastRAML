"""P10 — the validation pass.

Two questions, run over the same model. `check()` asks whether a *declaration*
is self-consistent (`minLength: 10, maxLength: 5`); `validate(value)` asks
whether *data* conforms (`example: "abc"` against `minLength: 10`). The per-kind
rules for both are methods on the kinds; this module is the driver that visits
every declaration, plus the parts that belong to no single kind — examples,
defaults, custom facets, and annotations.

Everything is driven off `Raml.fragment_typedefs`, the flat per-file index the
decoders fill with every top-level declaration. Nested shapes are reached from
their parents, so there is no graph traversal here either.

See docs/10-validation.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyraml.errors import Accumulator, ErrorKind, RamlError
from pyraml.types.complex_ import ArrayShape, ObjectShape, RecursiveShape, UnionShape
from pyraml.types.unwrap import mark_recursions, unwrap_shape
from pyraml.types.values import failure

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any

    from pyraml.datanode import DataNode
    from pyraml.parser.annotations import DomainExtension
    from pyraml.registry import Raml
    from pyraml.types.base import BaseShape, Property
    from pyraml.types.examples import Example

__all__ = ['check_declared_discriminators', 'validate_shapes']

#: Discriminator facet name → every value that names a type declaring it.
type DiscriminatorIndex = dict[str, set[str]]


# -- a rule that cannot wait for P10 (docs/05 section 9) -----------------------


def check_declared_discriminators(raml: Raml) -> None:
    """Spec § Using Discriminator: neither facet may be used on an **inline**
    type declaration.

    Run between P7 and P9, not with the rest of P10, and that ordering is the
    whole difficulty. `discriminator` is inherited: a body written `type: Person`
    against a discriminated `Person` carries one after unwrap, and it is inline —
    so the flattened model reports every correct document as broken. The
    reference implementation carries this as a `FIXME` for exactly that reason
    and enforces nothing.

    On the declared model the question is decidable: a discriminator is present
    only where it was written. "Inline" is then everything that is not a named
    type — `types:`, `schemas:`, `annotationTypes:` or a DataType fragment's
    root.
    """
    accumulator = Accumulator()
    for base in sorted(raml._discriminator_shapes, key=lambda candidate: candidate.id):  # noqa: SLF001
        shape = base.shape
        if isinstance(shape, ObjectShape) and not _is_named_type(raml, base):
            for facet, position in (
                ('discriminator', shape.discriminator.key_pos if shape.discriminator is not None else None),
                (
                    'discriminatorValue',
                    shape.discriminator_value.key_pos if shape.discriminator_value is not None else None,
                ),
            ):
                if position is not None:
                    accumulator.add(
                        failure(
                            'discriminator on an inline type declaration',
                            base.location,
                            position,
                            info={'facet': facet},
                        )
                    )
    accumulator.raise_if_any()


def _is_named_type(raml: Raml, base: BaseShape) -> bool:
    """Whether an indexed candidate is one of the parse's named declarations."""
    if base.name is not None:
        for index in (raml.fragment_types, raml.fragment_annotations):
            declared = index.get(base.location)
            named = None if declared is None else declared.get(base.name)
            if named is not None and named.id == base.id:
                return True
    fragment = raml.fragments.get(base.location)
    named = None if fragment is None else getattr(fragment, 'shape', None)
    return named is not None and named.id == base.id


def validate_shapes(raml: Raml) -> None:
    """Check every declaration, then every annotation application.

    One accumulator across both halves: a document with a bad example and an
    undeclared annotation should report both, not the first.
    """
    cache: dict[int, BaseShape] = {}
    accumulator = Accumulator()
    _validate_types(raml, cache, accumulator)
    _validate_domain_extensions(raml, cache, accumulator)
    accumulator.raise_if_any()


def _validate_types(raml: Raml, cache: dict[int, BaseShape], acc: Accumulator) -> None:
    known = _discriminator_values(raml, cache)
    for location, shapes in raml.fragment_typedefs.items():
        for base in shapes:
            try:
                flattened = _ensure_unwrapped(raml, base, cache)
            except RamlError as err:
                acc.add(RamlError.wrap('unwrap for validation', err, location, base.key_pos))
                continue
            try:
                flattened.check()
            except RamlError as err:
                acc.add(err)
            _validate_commons(flattened, known, acc, set())


def _ensure_unwrapped(raml: Raml, base: BaseShape, cache: dict[int, BaseShape]) -> BaseShape:
    """The flattened form of `base`, without flattening the caller's model.

    This is why `validate=True` works without `unwrap=True`: an un-unwrapped
    shape is copied detached, the **copy** is unwrapped and recursion-marked,
    and the copy is cached by the original's id. The declared model the caller
    sees keeps its `inherits` and its `link`.

    It costs one deep copy per declared type, which is why doc 13 section 4
    tells callers to pass both options together when they do not need the
    un-flattened view.
    """
    if base._unwrapped:  # noqa: SLF001 - P9 owns the field; this pass reads it
        return base
    cached = cache.get(base.id)
    if cached is not None:
        return cached
    copy = unwrap_shape(raml, base.clone_detached())
    mark_recursions(raml, roots=[copy])
    cache[base.id] = copy
    return copy


def _validate_commons(base: BaseShape, known: DiscriminatorIndex, acc: Accumulator, seen: set[int]) -> None:
    """Examples, defaults and custom facets, at this level and below.

    `seen` is by `BaseShape` identity rather than `id`: after unwrap a cycle is
    a `RecursiveShape` back-edge, but a *diamond* is still two paths to one
    object, and validating it twice would report the same failure twice.
    """
    if id(base) in seen:
        return
    seen.add(id(base))

    _validate_examples(base, known, acc)
    _validate_custom_facets(base, acc)

    shape = base.shape
    if isinstance(shape, ObjectShape):
        for prop in (shape.properties or {}).values():
            _validate_commons(prop.base, known, acc, seen)
        for pattern in (shape.pattern_properties or {}).values():
            _validate_commons(pattern.base, known, acc, seen)
    elif isinstance(shape, ArrayShape):
        if shape.items is not None:
            _validate_commons(shape.items, known, acc, seen)
    elif isinstance(shape, UnionShape):
        for member in shape.any_of or ():
            _validate_commons(member, known, acc, seen)
    for prop in base.custom_facet_defs.values():
        _validate_commons(prop.base, known, acc, seen)


# -- discriminator values (docs/05 section 9) ----------------------------------


def _discriminator_values(raml: Raml, cache: dict[int, BaseShape]) -> DiscriminatorIndex:
    """Discriminator name → every value that names a type declaring it.

    `discriminatorValue` defaults to the type's own name, so a type that carries
    a discriminator is always in its own set.

    **Keyed by the facet's name, not by the parent shape.** After P9 a type has
    its parent's discriminator but no `inherits` edge left to find the parent
    by, and the shape that actually needs the lookup is usually anonymous —
    `type: Person[]` gives its items a nameless shape. Keying by name loses one
    distinction: two unrelated hierarchies that both discriminate on `kind`
    share a set, so an instance of one may borrow the other's value. That error
    is permissive, never a false rejection, which is the right direction for a
    check that runs whatever `strict` says.

    The shapes are unwrapped through the same cache the validation pass uses, so
    the copies are shared rather than made twice, and so the index is the same
    with `unwrap=True` and without it.
    """
    index: DiscriminatorIndex = {}
    for source in (raml.fragment_types, raml.fragment_annotations):
        for declared in source.values():
            for name, base in declared.items():
                try:
                    flattened = _ensure_unwrapped(raml, base, cache)
                except RamlError:
                    # The failure is reported by the pass that unwraps for
                    # validation; this index simply has nothing to add.
                    continue
                shape = flattened.shape
                if not isinstance(shape, ObjectShape) or shape.discriminator is None:
                    continue
                value = str(shape.discriminator_value.raw) if shape.discriminator_value is not None else name
                index.setdefault(shape.discriminator.value, set()).add(value)
    return index


def _check_discriminator_values(
    base: BaseShape, data: DataNode | None, known: DiscriminatorIndex, acc: Accumulator
) -> None:
    """Every discriminator value in an example must name a type that exists.

    Run **outside** the `strict` gate. `strict: false` waives conformance --
    "this example deliberately does not validate" -- and a value that names no
    type is a different question: it is about the declaration graph, not about
    the instance. The TCK's `EdgeCases/identifying-discriminator` pair turns on
    exactly that, its two fixtures differing in one word with `strict: false`
    set in both.
    """
    if data is None or not known:
        return
    _walk_discriminators(base, data.raw, known, data, acc, '$', 0)


def _walk_discriminators(  # noqa: PLR0913, PLR0917 - a data walk carries shape, value, index and position
    base: BaseShape,
    value: Any,
    known: DiscriminatorIndex,
    data: DataNode,
    acc: Accumulator,
    path: str,
    depth: int,
) -> None:
    if depth > base._raml.max_depth:  # noqa: SLF001 - the parse's ceiling (docs/12 § 14)
        return
    shape = base.shape
    if isinstance(shape, RecursiveShape):
        _walk_discriminators(shape.head, value, known, data, acc, path, depth + 1)
        return
    if isinstance(shape, ArrayShape) and isinstance(value, list) and shape.items is not None:
        for index, item in enumerate(value):
            _walk_discriminators(shape.items, item, known, data, acc, f'{path}[{index}]', depth + 1)
        return
    if not isinstance(shape, ObjectShape) or not isinstance(value, dict):
        return

    if shape.discriminator is not None:
        _check_one(shape.discriminator.value, value, known, data, acc, path)
    for name, prop in (shape.properties or {}).items():
        if name in value:
            _walk_discriminators(prop.base, value[name], known, data, acc, f'{path}.{name}', depth + 1)


def _check_one(  # noqa: PLR0913, PLR0917 - as above
    discriminator: str,
    value: dict,
    known: DiscriminatorIndex,
    data: DataNode,
    acc: Accumulator,
    path: str,
) -> None:
    written = value.get(discriminator)
    if not isinstance(written, str):
        # Absent, or the wrong type: both are ordinary validation's business.
        return
    allowed = known.get(discriminator)
    if not allowed or written in allowed:
        return
    acc.add(
        failure(
            'discriminator value names no known type',
            data.location,
            data.value_pos,
            info={'path': path, 'discriminator': discriminator, 'value': written, 'known': sorted(allowed)},
        )
    )


# -- examples, defaults, enums (docs/10 section 3) -----------------------------


def _each_example(base: BaseShape) -> Iterator[Example]:
    if base.example is not None:
        yield base.example
    if base.examples is not None:
        yield from base.examples.entries().values()


def _validate_examples(base: BaseShape, known: DiscriminatorIndex, acc: Accumulator) -> None:
    for example in _each_example(base):
        # Before the `strict` gate, and outside it: naming a type that does not
        # exist is not a conformance failure the author may waive (§ 9 of
        # docs/05-type-model.md).
        _check_discriminator_values(base, example.data, known, acc)
    if base.example is not None:
        _validate_example(base, base.example, acc)
    if base.examples is not None:
        for example in base.examples.entries().values():
            _validate_example(base, example, acc)
    if base.default is not None:
        # No `strict` for a default: an unusable default is always a defect,
        # whereas an example may deliberately show a malformed payload.
        try:
            base.validate_at(base.default.raw, '$')
        except RamlError as err:
            acc.add(
                RamlError.wrap(
                    'invalid default',
                    err,
                    base.default.location,
                    base.default.value_pos,
                    kind=ErrorKind.VALIDATING,
                )
            )


def _validate_example(base: BaseShape, example: Example, acc: Accumulator) -> None:
    if example.strict is not None and not example.strict.value:
        return
    if example.data is None:
        return
    try:
        base.validate_at(example.data.raw, '$')
    except RamlError as err:
        acc.add(
            RamlError.wrap(
                'invalid example',
                err,
                example.data.location,
                example.data.value_pos,
                kind=ErrorKind.VALIDATING,
                info={'example': example.name} if example.name else None,
            )
        )


# -- custom facets (docs/10 section 4) -----------------------------------------


def _facet_declarations(base: BaseShape, acc: Accumulator) -> dict[str, Property]:
    """Every `facets:` declaration this shape must satisfy, nearest first.

    **The walk starts at the parent, not at `base`.** A `facets:` block declares
    what *subtypes* must supply; the declaring type neither has to satisfy its
    own required facets nor may supply a value for one — go-raml calls that
    `unknown facet`, and both halves are measured behaviour, not inference.

    **Known limitation, inherited from the reference implementation:** the walk
    follows `inherits[0]` only, so a facet declared on the second parent of a
    multiply-inheriting type is not seen. Fixing it means walking all parents
    with a visited set; it is tracked as a v1.1 item in doc 10 section 4, and a
    test pins the current behaviour so the fix is visible when it lands.
    """
    declared: dict[str, Property] = {}
    current: BaseShape | None = base.inherits[0] if base.inherits else None
    depth = 0
    while current is not None and depth < base._raml.max_depth:  # noqa: SLF001 - as above
        for name, prop in current.custom_facet_defs.items():
            if name in declared:
                acc.add(
                    failure(
                        'duplicate custom facet',
                        current.location,
                        prop.base.key_pos,
                        info={'facet': name},
                    )
                )
                continue
            declared[name] = prop
        current = current.inherits[0] if current.inherits else None
        depth += 1
    return declared


def _validate_custom_facets(base: BaseShape, acc: Accumulator) -> None:
    # A union is checked like anything else. Until the distribution landed it
    # was skipped, because a facet written beside `type: A|B` had no kind to be
    # decoded against and reached `custom_facets` even when it was a built-in
    # facet of the members — so `unknown facet` here would have rejected what
    # the spec allows. P9 now hands each facet to the members instead, and what
    # reaches this point on a union is a facet with nowhere to go
    # (docs/07 section 3.4).
    declared = _facet_declarations(base, acc)
    for name, prop in declared.items():
        if prop.required and name not in base.custom_facets:
            acc.add(
                failure(
                    'required custom facet is missing',
                    base.location,
                    base.value_pos,
                    info={'facet': name},
                )
            )
    for name, value in base.custom_facets.items():
        supplied = declared.get(name)
        if supplied is None:
            # This is what turns a typo into an error. An unrecognised facet key
            # became a custom facet *value* during decoding (docs/05 section 4),
            # and nothing before now could tell `maxLenght` from a real one.
            acc.add(failure('unknown facet', value.location, value.key_pos, info={'facet': name}))
            continue
        try:
            supplied.base.validate_at(value.raw, f'${name}')
        except RamlError as err:
            acc.add(
                RamlError.wrap(
                    'invalid custom facet value',
                    err,
                    value.location,
                    value.value_pos,
                    kind=ErrorKind.VALIDATING,
                    info={'facet': name},
                )
            )


# -- annotations (docs/09 sections B4 and B5) ----------------------------------


def _validate_domain_extensions(raml: Raml, cache: dict[int, BaseShape], acc: Accumulator) -> None:
    """P8 bound each application; this is the only consumer of that binding."""
    for extension in raml.domain_extensions:
        if extension.defined_by is None:
            # P8 already reported it. Reporting again would double every
            # undeclared annotation in the output.
            continue
        try:
            declared = _ensure_unwrapped(raml, extension.defined_by, cache)
        except RamlError as err:
            acc.add(RamlError.wrap('unwrap annotation type', err, extension.location, extension.key_pos))
            continue
        _check_target(extension, declared, acc)
        try:
            declared.validate_at(extension.value.raw, '$')
        except RamlError as err:
            acc.add(
                RamlError.wrap(
                    'invalid annotation value',
                    err,
                    extension.location,
                    extension.value_pos,
                    kind=ErrorKind.VALIDATING,
                    info={'annotation': extension.name},
                )
            )


def _check_target(extension: DomainExtension, declared: BaseShape, acc: Accumulator) -> None:
    """`allowedTargets`, which the reference implementation parses and ignores.

    `None` and `[]` mean different things and the difference is load-bearing:
    absent allows every target, empty allows none (docs/09 section B5).
    """
    allowed = declared.allowed_targets
    if allowed is None or extension.target in allowed:
        return
    acc.add(
        failure(
            'annotation not allowed at this target',
            extension.location,
            extension.key_pos,
            info={
                'annotation': extension.name,
                'target': str(extension.target),
                'allowed': [str(target) for target in allowed],
            },
        )
    )

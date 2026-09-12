"""External JSON Schema types — `JsonShape` and the registry behind it.

docs/10-validation.md section 6. A RAML type whose `type:` is a JSON document
rather than a type name delegates both halves of validation to a compiled JSON
Schema: `check()` is done at construction, and `validate()` runs the instance
against the compiled validator.

One `SchemaRegistry` per parse, so a `$ref` target shared by forty schemas is
read and parsed once (section 6.1). Every read goes through `Raml.loader`, which
is what keeps the workspace sandbox and the remote-includes switch in force: a
`$ref` to `http://json-schema.org/...` in an offline parse fails loudly rather
than reaching the network.

`JsonShape` lives here rather than beside the other structured kinds because
compiling a schema needs `referencing` and the loader, and the section 6.3
projection needs to *build* object, array and union shapes — so this module sits
above `complex_.py` and `scalars.py` and is imported by `shape.py`
(docs/02-architecture.md section 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, Any, Final

from pyraml.datanode import DataNode, value_node_of
from pyraml.errors import ErrorKind, RamlError
from pyraml.parser.facets import regex_engine
from pyraml.types.base import (
    TYPE_ANY,
    TYPE_ARRAY,
    TYPE_BOOLEAN,
    TYPE_INTEGER,
    TYPE_NIL,
    TYPE_NUMBER,
    TYPE_OBJECT,
    TYPE_RECURSIVE,
    TYPE_STRING,
    TYPE_UNION,
    BaseShape,
    PatternProperty,
    Property,
    ScalarFacet,
)
from pyraml.types.complex_ import ArrayShape, ComplexKind, ObjectShape, RecursiveShape, UnionShape
from pyraml.types.examples import Example, Examples
from pyraml.types.inherit import inherit
from pyraml.types.scalars import AnyShape, BooleanShape, IntegerShape, NilShape, NumberShape, StringShape
from pyraml.uris import uri_base
from pyraml.yamlnode import node_error

if TYPE_CHECKING:
    # Annotations only. Since Phase 9 this module compiles no pattern itself:
    # every one goes through `regex_engine`, so the parse's choice applies here
    # too (docs/01 deviation D3).
    import re

    from referencing import Registry, Resource
    from referencing._core import Resolver

    from pyraml.positions import Position
    from pyraml.registry import Raml
    from pyraml.types.base import Shape
    from pyraml.yamlnode import Node

__all__ = [
    'CompiledSchema',
    'JsonShape',
    'SchemaRegistry',
    'schema_registry',
]

#: Keywords whose values are user data rather than subschemas. A `$ref` written
#: inside a `default` is a value that happens to look like a reference, and
#: resolving it would reject a document the spec allows.
_DATA_KEYWORDS: Final = frozenset({'const', 'default', 'enum', 'example', 'examples'})

#: Keywords whose value is a *mapping of* subschemas. The mapping itself is not
#: a schema, so its keys must not be read as keywords.
_SCHEMA_MAPS: Final = frozenset(
    {'$defs', 'definitions', 'dependencies', 'dependentSchemas', 'patternProperties', 'properties'}
)


@dataclass(frozen=True, slots=True)
class CompiledSchema:
    """One compiled schema, and the two things the projection needs from it.

    The validator alone would do for `validate()`, but § 6.3 walks the schema
    document itself and follows its `$ref`s, and both of those live behind
    private attributes of the validator.
    """

    validator: Any
    contents: Any
    resolver: Resolver[Any]


class _LoadFailure(Exception):  # noqa: N818 - not an error surface; a carrier
    """Carries a `RamlError` out through `referencing`'s retrieval hook.

    `Registry.get_or_retrieve` re-raises anything unexpected as `Unretrievable`
    with the original as `__cause__`, so the diagnostic the loader produced —
    "no loader for URI scheme 'https'" is the one that matters — is recovered by
    walking that chain rather than reconstructed from the reference alone.
    """

    __slots__ = ('error',)

    def __init__(self, error: RamlError) -> None:
        super().__init__(str(error))
        self.error = error


class SchemaRegistry:
    """The JSON Schema resources of one parse (docs/10 section 6.1).

    Held on `Raml`, built on first use. The cache is what makes a shared `$ref`
    target linear rather than quadratic: `referencing`'s own registry is a
    persistent structure whose retrievals are discarded with the copy that made
    them, so the memo has to live here.
    """

    __slots__ = ('_raml', '_resources')

    def __init__(self, raml: Raml) -> None:
        self._raml = raml
        self._resources: dict[str, Resource[Any]] = {}

    @property
    def fetched(self) -> int:
        """How many distinct URIs have been read. Test surface, not model state."""
        return len(self._resources)

    def compile(self, raw: str, location: str, position: Position | None) -> CompiledSchema:
        """Compile one schema, resolving every reference it names.

        `location` may carry a JSON Pointer (`schema.json#/definitions/User`),
        which selects the subschema the RAML type stands for; the whole document
        is still the compilation unit, so a sibling definition it references
        resolves.
        """
        # JSON Schema is uncommon in ordinary RAML documents. Keep its sizeable
        # dependency tree off the startup path until a schema is actually used.
        from jsonschema.exceptions import SchemaError  # noqa: PLC0415 - deferred for startup cost
        from jsonschema.validators import Draft7Validator, validator_for  # noqa: PLC0415
        from referencing import Registry, Resource  # noqa: PLC0415

        document_uri, _, pointer = location.partition('#')
        contents = self._decode(raw, location, position)
        specification = _specification_of(contents)
        validator_class = validator_for(contents, default=Draft7Validator)
        try:
            validator_class.check_schema(contents)
        except SchemaError as err:
            raise RamlError.new(
                'invalid JSON schema',
                location,
                position,
                kind=ErrorKind.PARSING,
                info={'error': err.message, 'path': '/'.join(str(part) for part in err.absolute_path)},
            ) from err

        # `retrieve` is the init alias of the private `_retrieve` field, which
        # mypy's attrs plugin does not derive.
        registry: Registry[Any] = Registry(retrieve=self._retrieve).with_resource(  # type: ignore[call-arg]
            document_uri, Resource.from_contents(contents, default_specification=specification)
        )
        resolver = registry.resolver(document_uri)
        if pointer:
            resolved = self._lookup(resolver, '#' + pointer, location, position)
            contents, resolver = resolved.contents, resolved.resolver
        self._prefetch(contents, resolver, specification, location, position, set())
        return CompiledSchema(
            validator=validator_class(contents, registry=registry, _resolver=resolver),
            contents=contents,
            resolver=resolver,
        )

    # -- reading --------------------------------------------------------------

    def _decode(self, raw: str | bytes, location: str, position: Position | None = None) -> Any:
        import json  # noqa: PLC0415 - loaded with the deferred JSON Schema dependencies

        try:
            contents = json.loads(raw)
        except ValueError as err:
            raise RamlError.new(
                'invalid JSON in schema', location, position, kind=ErrorKind.PARSING, info={'error': str(err)}
            ) from err
        self._check_nesting(contents, location, position)
        return contents

    def _check_nesting(self, contents: Any, location: str, position: Position | None) -> None:
        """Refuse a schema nested past the parse's ceiling, before anything walks it.

        Three separate recursions run over a decoded schema — `check_schema`
        inside the schema library, `_prefetch` here, and the § 6.3 projection —
        and the first of them is not ours to guard from the inside. A 200-level
        schema exhausts CPython's stack inside `jsonschema`'s meta-schema
        validation and surfaces as `RecursionError`, which docs/12 section 14
        forbids outright. Measuring the depth first is one iterative pass over a
        document already in memory, and it makes all three safe at once.
        """
        limit = self._raml.max_depth
        stack: list[tuple[Any, int]] = [(contents, 0)]
        while stack:
            node, depth = stack.pop()
            if depth > limit:
                raise RamlError.new(
                    'JSON schema nesting too deep',
                    location,
                    position,
                    kind=ErrorKind.PARSING,
                    info={'limit': limit},
                )
            if isinstance(node, dict):
                stack.extend((value, depth + 1) for value in node.values())
            elif isinstance(node, list):
                stack.extend((item, depth + 1) for item in node)

    def _retrieve(self, uri: str) -> Resource[Any]:
        """`referencing`'s hook: every `$ref` target is read through the loader."""
        from referencing import Resource  # noqa: PLC0415 - deferred for startup cost
        from referencing.jsonschema import DRAFT7  # noqa: PLC0415

        cached = self._resources.get(uri)
        if cached is not None:
            return cached
        raml = self._raml
        limit = raml.max_include_size
        try:
            data = raml.loader.load(uri, max_bytes=limit if limit > 0 else None)
        except OSError as err:
            raise _LoadFailure(
                RamlError.wrap('load JSON schema', err, uri, kind=ErrorKind.LOADING, info={'path': uri})
            ) from err
        if 0 < limit < len(data):
            raise _LoadFailure(
                RamlError.new(
                    'JSON schema exceeds size limit', uri, kind=ErrorKind.LOADING, info={'path': uri, 'limit': limit}
                )
            )
        try:
            # The same decode as the entry schema's, so a `$ref` target is held
            # to the nesting ceiling too: without that, a shallow schema could
            # point at a 500-level one and reach the stack anyway.
            contents = self._decode(data, uri)
        except RamlError as err:
            raise _LoadFailure(err) from err
        resource = Resource.from_contents(contents, default_specification=DRAFT7)
        self._resources[uri] = resource
        return resource

    # -- reference resolution -------------------------------------------------

    def _lookup(self, resolver: Resolver[Any], ref: str, location: str, position: Position | None) -> Any:
        from referencing.exceptions import Unresolvable  # noqa: PLC0415 - deferred for startup cost

        try:
            return resolver.lookup(ref)
        except Unresolvable as err:
            raise self._reference_error(err, ref, location, position) from err

    @staticmethod
    def _reference_error(err: Exception, ref: str, location: str, position: Position | None) -> RamlError:
        """The loader's own diagnostic when there is one, the reference otherwise."""
        cause: BaseException | None = err
        while cause is not None:
            if isinstance(cause, _LoadFailure):
                return RamlError.wrap(
                    'unresolvable JSON schema reference',
                    cause.error,
                    location,
                    position,
                    kind=ErrorKind.PARSING,
                    info={'ref': ref},
                )
            cause = cause.__cause__
        return RamlError.new(
            'unresolvable JSON schema reference',
            location,
            position,
            kind=ErrorKind.PARSING,
            info={'ref': ref, 'error': str(err)},
        )

    def _prefetch(  # noqa: PLR0913, PLR0917 - the walk carries its resolver, its spec and its diagnostic context
        self,
        node: Any,
        resolver: Resolver[Any],
        specification: Any,
        location: str,
        position: Position | None,
        seen: set[int],
        depth: int = 0,
    ) -> None:
        """Resolve every `$ref` now rather than at first validation.

        A schema library resolves lazily, so a reference to a missing file in a
        type nothing validates against would never be reported. The reference
        implementation compiles eagerly and the TCK expects that, so the walk is
        done here — best-effort by design: it skips the keywords whose values are
        data (`_DATA_KEYWORDS`) and the ones whose values are maps of subschemas
        (`_SCHEMA_MAPS`), which is enough to keep a user value that looks like a
        reference from being resolved as one.

        `depth` counts levels of this recursion and `seen` counts documents; they
        are not interchangeable. `seen` stops a `$ref` cycle from running away
        and never shrinks, so a schema with 300 distinct references — perfectly
        ordinary — has a `seen` of 300 at a depth of two.
        """
        if depth > self._raml.max_depth:
            # `_check_nesting` already bounded each document on its own. What is
            # left to bound is a chain of `$ref`s through many shallow documents,
            # which nests as deep as the chain is long.
            raise RamlError.new(
                'JSON schema nesting too deep',
                location,
                position,
                kind=ErrorKind.PARSING,
                info={'limit': self._raml.max_depth},
            )
        if isinstance(node, list):
            for item in node:
                self._prefetch(item, resolver, specification, location, position, seen, depth + 1)
            return
        if not isinstance(node, dict):
            return

        resource = specification.create_resource(node)
        if resource.id() is not None:
            resolver = resolver.in_subresource(resource)

        ref = node.get('$ref')
        if isinstance(ref, str):
            resolved = self._lookup(resolver, ref, location, position)
            if id(resolved.contents) not in seen:
                seen.add(id(resolved.contents))
                self._prefetch(resolved.contents, resolved.resolver, specification, location, position, seen, depth + 1)

        for key, value in node.items():
            if key == '$ref' or key in _DATA_KEYWORDS:
                continue
            if key in _SCHEMA_MAPS and isinstance(value, dict):
                for member in value.values():
                    self._prefetch(member, resolver, specification, location, position, seen, depth + 1)
            else:
                self._prefetch(value, resolver, specification, location, position, seen, depth + 1)


def _specification_of(contents: Any) -> Any:
    """The draft a schema declares, or 7 — what the reference implementation assumes."""
    from referencing.jsonschema import DRAFT7, specification_with  # noqa: PLC0415 - deferred for startup cost

    declared = contents.get('$schema') if isinstance(contents, dict) else None
    if not isinstance(declared, str):
        return DRAFT7
    try:
        return specification_with(declared)
    except Exception:  # noqa: BLE001 - an unknown draft is not fatal; the meta-schema check reports it
        return DRAFT7


def schema_registry(raml: Raml) -> SchemaRegistry:
    """The parse's registry, built on first use.

    Not built in `Raml.__init__`: `registry.py` imports nothing from `types/` at
    runtime, and that rule is what keeps the import graph acyclic
    (docs/02-architecture.md section 3).
    """
    existing = raml.json_schema_registry
    if existing is None:
        existing = SchemaRegistry(raml)
        raml.json_schema_registry = existing
    return existing


class JsonShape(ComplexKind):
    """A type declared by an external or inline JSON Schema.

    Spec section Using XML and JSON Schemas: such a type "MUST NOT participate
    in type inheritance or specialization". Half of that is enforced here — any
    sibling facet is an error. The wrapper facets the spec does allow
    (`displayName`, `description`, annotations, `example`/`examples`) are common
    facets, so `shape.py` has already taken them and they never arrive here.

    The schema is compiled at construction, not at `check()`: malformed JSON in
    a `type:` is a syntax error in the document, and reporting it only under
    `validate=True` would let a broken schema through the default parse.
    """

    __slots__ = ('_cached_defs', '_cached_schema', '_cached_shape', '_compiled', 'raw', 'validator')

    def __init__(self, base: BaseShape, raw: str = '') -> None:
        super().__init__(base)
        #: The schema exactly as written.
        self.raw = raw
        self.validator: Any = None
        self._compiled: CompiledSchema | None = None
        self._cached_shape: BaseShape | None = None
        self._cached_defs: dict[str, BaseShape] | None = None
        self._cached_schema: Any | None = None
        if raw:
            self._compiled = schema_registry(base._raml).compile(raw, base.location, base.key_pos)  # noqa: SLF001
            self.validator = self._compiled.validator

    def decode_facets(self, pairs: list[Node]) -> None:
        if pairs:
            raise node_error(
                'cannot define facets on a JSON schema type',
                self.base.location,
                pairs[0],
                info={'facet': pairs[0].value},
            )

    def check(self) -> None:
        # Compilation happened at construction and is the whole of this kind's
        # declaration check; there are no RAML facets left to be inconsistent.
        return

    def validate(self, value: Any, path: str) -> None:
        if self.validator is None:
            return
        from jsonschema.exceptions import ValidationError  # noqa: PLC0415 - deferred for startup cost
        from referencing.exceptions import Unresolvable  # noqa: PLC0415

        try:
            self.validator.validate(value)
        except ValidationError as err:
            raise RamlError.new(
                'value does not match the JSON schema',
                self.base.location,
                self.base.value_pos,
                kind=ErrorKind.VALIDATING,
                info={
                    'path': path,
                    'error': err.message,
                    'schema_path': '/'.join(str(part) for part in err.absolute_schema_path),
                },
            ) from err
        except Unresolvable as err:
            raise SchemaRegistry._reference_error(  # noqa: SLF001 - one diagnostic, two call sites
                err, str(getattr(err, 'ref', '')), self.base.location, self.base.value_pos
            ) from err

    # -- the projection (docs/10 section 6.3) ---------------------------------

    def as_shape(self) -> BaseShape | None:
        """The nearest RAML shape to this schema, built once and cached.

        A **view** object, for consumers that want one model rather than two. It
        is not in `Raml.shapes`, it carries no positions, and it is marked
        unwrapped. Feeding one back into the parser's own passes is the failure
        mode to avoid: the model looks right until P9 tries to flatten it.
        """
        if self._compiled is None:
            return None
        if self._cached_shape is None:
            defs: dict[str, BaseShape] = {}
            self._cached_shape = _project(
                _Projection(self.base, self._compiled.resolver, defs), self._compiled.contents, {}
            )
            self._cached_defs = defs
        return self._cached_shape

    def as_shape_defs(self) -> dict[str, BaseShape] | None:
        """The named `$ref` targets `as_shape` extracted, in encounter order.

        `None` until `as_shape` has run — the two are one traversal.
        """
        return self._cached_defs

    def as_schema(self) -> Any | None:
        """The schema as one self-contained document, built once and cached.

        Every reference a reader cannot follow is pulled in: a `$ref` naming
        another file names nothing they have, and a schema carrying one
        describes a type only to someone holding the rest of the directory it
        was written in. What comes back is the schema in the same vocabulary the
        author used — which is the point of showing a schema at all, the RAML
        reading of it being `as_shape` — and it validates the same documents.

        A pointer *within* the document stays a pointer. It is followable where
        it stands, inlining it loses the sharing the author expressed, and
        `#/definitions/node` inside `node` has no finite expansion.
        """
        if self._compiled is None:
            return None
        if self._cached_schema is None:
            self._cached_schema = _bundle(self._compiled)
        return self._cached_schema


def projected(base: BaseShape) -> BaseShape:
    """`base` as a consumer walking structure should see it.

    A JSON-schema type through its § 6.3 projection, anything else unchanged.
    One function rather than the same `isinstance(shape, JsonShape)` in every
    consumer, because a consumer that forgets it does not fail — it sees a leaf
    with no properties, no items and no facets, and reports that a schema type
    is made of nothing.

    Lives here rather than on `BaseShape`, which cannot import this module, and
    rather than in a consumer, which would make the substitution one module's
    private opinion. Rendering and traversal only: `as_shape` explains why a
    view must never be fed back into a pass.
    """
    if isinstance(base.shape, JsonShape):
        return base.shape.as_shape() or base
    return base


# -- section 6.3: JSON Schema -> the nearest RAML shape -------------------------


@dataclass(frozen=True, slots=True)
class _Projection:
    """What every level of the walk shares: where to hang the view shapes."""

    parent: BaseShape
    resolver: Resolver[Any]
    defs: dict[str, BaseShape]

    def at(self, resolver: Resolver[Any]) -> _Projection:
        return _Projection(self.parent, resolver, self.defs)


def _unsupported(context: _Projection, what: str) -> RamlError:
    return RamlError.new(
        'JSON schema construct has no RAML equivalent',
        context.parent.location,
        context.parent.value_pos,
        kind=ErrorKind.RESOLVING,
        info={'construct': what},
    )


def _view_base(context: _Projection, name: str | None = None) -> BaseShape:
    """A `BaseShape` outside the parse's own bookkeeping.

    Deliberately not `put_shape`d and not `put_typedef`d: these are not
    declarations the document made, and P9 and P10 must never reach them.
    Marked unwrapped so a consumer serialising the model emits the concrete type
    inline rather than an inheritance link that leads nowhere.
    """
    base = BaseShape(
        id=context.parent._raml.next_id(),  # noqa: SLF001 - one counter per parse (docs/02 § 3.1)
        raml=context.parent._raml,  # noqa: SLF001 - as above
        location=context.parent.location,
        name=name,
    )
    base._unwrapped = True  # noqa: SLF001 - a view shape has nothing left to flatten
    return base


def _project(context: _Projection, contents: Any, visiting: dict[int, BaseShape]) -> BaseShape:
    """One schema node, as the table in docs/10 section 6.3 maps it."""
    # `visiting` holds one entry per level currently open — it is added to
    # before descending and removed in a `finally` — so its size *is* the depth,
    # and the guard costs a `len`. Each document was already bounded by
    # `_check_nesting`; what this catches is a chain of `$ref`s across many
    # shallow documents, which nests as deep as the chain is long.
    if len(visiting) > context.parent._raml.max_depth:  # noqa: SLF001 - the parse's ceiling (docs/12 § 14)
        raise RamlError.new(
            'JSON schema nesting too deep',
            context.parent.location,
            context.parent.value_pos,
            kind=ErrorKind.RESOLVING,
            info={'limit': context.parent._raml.max_depth},  # noqa: SLF001 - as above
        )
    if contents is False:
        raise _unsupported(context, 'false schema')
    if contents is True or not isinstance(contents, dict):
        return _kind(_view_base(context), TYPE_ANY, AnyShape)

    reference = contents.get('$ref')
    if isinstance(reference, str):
        return _project_reference(context, reference, visiting)
    if 'if' in contents:
        raise _unsupported(context, 'if/then/else')

    base = _decorate(_view_base(context), contents)
    visiting[id(contents)] = base
    try:
        return _project_body(context, contents, base, visiting)
    finally:
        del visiting[id(contents)]


def _project_reference(context: _Projection, reference: str, visiting: dict[int, BaseShape]) -> BaseShape:
    resolved = context.resolver.lookup(reference)
    head = visiting.get(id(resolved.contents))
    if head is not None:
        # The back-edge of a cycle, which is exactly what P9 produces for a
        # recursive RAML type (docs/07 section 4).
        base = _view_base(context, head.name)
        base.type = TYPE_RECURSIVE
        base.shape = RecursiveShape(base, head)
        return base

    name = _definition_name(reference)
    if name is not None:
        existing = context.defs.get(name)
        if existing is not None:
            return existing
    built = _project(context.at(resolved.resolver), resolved.contents, visiting)
    if name is not None:
        built.name = name
        context.defs[name] = built
    return built


def _definition_name(reference: str) -> str | None:
    """The last segment of a pointer that names a definition, if it does.

    `#/definitions/User` names `User`; `#` and `other.json` name nothing, and a
    shape built for one of those is inlined rather than registered.
    """
    pointer = reference.partition('#')[2]
    if not pointer.startswith('/'):
        return None
    segment = pointer.rsplit('/', 1)[-1]
    return segment or None


def _project_body(context: _Projection, contents: dict, base: BaseShape, visiting: dict[int, BaseShape]) -> BaseShape:
    if contents.get('allOf'):
        return _project_all_of(context, contents['allOf'], base, visiting)
    for keyword in ('oneOf', 'anyOf'):
        # `oneOf`'s exactly-one semantics is lost. RAML's union is "at least
        # one" and there is nothing nearer; doc 10 § 6.3 records the loss.
        members = contents.get(keyword)
        if members:
            return _project_union(context, members, base, visiting)

    declared = contents.get('type') or _inferred_type(contents)
    if declared is None:
        return _kind(base, TYPE_ANY, AnyShape)
    if isinstance(declared, list):
        if len(declared) == 1:
            return _project_type(context, str(declared[0]), contents, base, visiting)
        # Each member carries only its type; the constraints stay on the union
        # base, because a JSON Schema states them once for every member.
        members = [_project_type(context, str(name), {}, _view_base(context), visiting) for name in declared]
        return _kind(base, TYPE_UNION, UnionShape, any_of=members)
    return _project_type(context, str(declared), contents, base, visiting)


#: JSON Schema keywords that apply to exactly one instance type.
#:
#: **Deliberately not RAML's `FACET_TYPE_HINT`**, though the two overlap. That
#: table is wrong here in both directions: it maps `fileTypes` and
#: `discriminator`, which are not JSON Schema keywords, and it omits
#: `patternProperties`, `required`, `dependencies`, `contains` and
#: `exclusiveMinimum`, which are. Its `identify_shape_type` also *raises* on a
#: declaration hinting at two kinds — and a JSON schema constraining two kinds at
#: once is legal and ordinary, so borrowing it would reject valid input.
_KEYWORD_TYPE: Final[dict[str, str]] = {
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


def _inferred_type(contents: dict) -> str | None:
    """The one kind a subschema constrains, when it constrains only one.

    **This is a projection decision, not JSON Schema semantics.** In JSON Schema
    a keyword is an assertion that applies *conditionally on the instance type*:
    `{"properties": {...}, "required": ["a"]}` does not say the instance is an
    object, it says that **if** it is one then `a` must be present. Against the
    string `"hello"` that schema passes, vacuously. Nothing is inferred, and
    validation here is unaffected — it goes to the real validator, which has
    those semantics.

    The § 6.3 *projection* has to pick a kind, because RAML has no way to spell
    "a constraint that applies only to objects, and is silent otherwise". When
    every keyword present points at one kind, that kind is the least-lossy pick.

    When they point at more than one — `{"properties": {...}, "minLength": 3}`
    constrains objects *and* strings, which is legal and which RAML cannot
    express — this returns `None` and the shape stays `any` rather than silently
    choosing. Losing the constraints is bad; claiming the wrong kind is worse.

    Written for `allOf`, where a member almost never repeats `"type"`: the
    enclosing schema already said it. Projecting such a member as `any` and then
    merging it made `inherit` refuse — "cannot inherit from different type" —
    which took down the projection of the whole schema.
    """
    implied = {_KEYWORD_TYPE[keyword] for keyword in contents if keyword in _KEYWORD_TYPE}
    return implied.pop() if len(implied) == 1 else None


def _project_all_of(context: _Projection, members: list, base: BaseShape, visiting: dict[int, BaseShape]) -> BaseShape:
    """Sequential inheritance, which is the nearest thing RAML has to `allOf`."""
    merged = _project(context, members[0], visiting)
    for member in members[1:]:
        merged = inherit(merged, _project(context, member, visiting))
    # The wrapper's own title, description, default and enum still apply.
    _decorate(merged, {})
    for field_name in ('display_name', 'description', 'default', 'enum'):
        value = getattr(base, field_name)
        if value is not None:
            setattr(merged, field_name, value)
    return merged


def _project_union(context: _Projection, members: list, base: BaseShape, visiting: dict[int, BaseShape]) -> BaseShape:
    return _kind(base, TYPE_UNION, UnionShape, any_of=[_project(context, member, visiting) for member in members])


def _project_type(  # noqa: PLR0911 - one return per row of the table in docs/10 § 6.3
    context: _Projection, declared: str, contents: dict, base: BaseShape, visiting: dict[int, BaseShape]
) -> BaseShape:
    match declared:
        case 'object':
            return _project_object(context, contents, base, visiting)
        case 'array':
            return _project_array(context, contents, base, visiting)
        case 'string':
            shape = StringShape(base)
            shape.min_length = _int_facet(base, contents.get('minLength'))
            shape.max_length = _int_facet(base, contents.get('maxLength'))
            shape.pattern = _pattern_facet(base, contents.get('pattern'))
            return _attach(base, TYPE_STRING, shape)
        case 'integer':
            integer = IntegerShape(base)
            integer.minimum = _int_facet(base, contents.get('minimum'))
            integer.maximum = _int_facet(base, contents.get('maximum'))
            integer.multiple_of = _fraction_facet(base, contents.get('multipleOf'))
            return _attach(base, TYPE_INTEGER, integer)
        case 'number':
            number = NumberShape(base)
            number.minimum = _fraction_facet(base, contents.get('minimum'))
            number.maximum = _fraction_facet(base, contents.get('maximum'))
            number.multiple_of = _fraction_facet(base, contents.get('multipleOf'))
            return _attach(base, TYPE_NUMBER, number)
        case 'boolean':
            return _kind(base, TYPE_BOOLEAN, BooleanShape)
        case 'null':
            return _kind(base, TYPE_NIL, NilShape)
        case _:
            raise _unsupported(context, f'type: {declared}')


def _project_object(context: _Projection, contents: dict, base: BaseShape, visiting: dict[int, BaseShape]) -> BaseShape:
    extras = contents.get('additionalProperties')
    if isinstance(extras, dict):
        raise _unsupported(context, 'schema-form additionalProperties')

    required = set(contents.get('required') or ())
    properties = {
        name: Property(name=name, base=_project(context, schema, visiting), required=name in required)
        for name, schema in (contents.get('properties') or {}).items()
    }
    patterns: dict[str, PatternProperty] = {}
    for text, schema in (contents.get('patternProperties') or {}).items():
        compiled = _compile(context, text)
        patterns[f'/{text}/'] = PatternProperty(pattern=compiled, base=_project(context, schema, visiting))

    shape = ObjectShape(base, properties=properties or None, pattern_properties=patterns or None)
    shape.min_properties = _int_facet(base, contents.get('minProperties'))
    shape.max_properties = _int_facet(base, contents.get('maxProperties'))
    if isinstance(extras, bool):
        shape.additional_properties = ScalarFacet(value=extras, location=base.location)
    return _attach(base, TYPE_OBJECT, shape)


def _project_array(context: _Projection, contents: dict, base: BaseShape, visiting: dict[int, BaseShape]) -> BaseShape:
    items = contents.get('items')
    if isinstance(items, list):
        raise _unsupported(context, 'tuple-form items')

    shape = ArrayShape(base, items=None if items is None else _project(context, items, visiting))
    shape.min_items = _int_facet(base, contents.get('minItems'))
    shape.max_items = _int_facet(base, contents.get('maxItems'))
    if contents.get('uniqueItems'):
        shape.unique_items = ScalarFacet(value=True, location=base.location)
    return _attach(base, TYPE_ARRAY, shape)


def _decorate(base: BaseShape, contents: dict) -> BaseShape:
    """The five annotation-ish keywords that map onto common RAML facets."""
    title = contents.get('title')
    if isinstance(title, str):
        base.display_name = ScalarFacet(value=title, location=base.location)
    description = contents.get('description')
    if isinstance(description, str):
        base.description = ScalarFacet(value=description, location=base.location)
    if 'default' in contents:
        base.default = _data(base, contents['default'])
    enum = contents.get('enum')
    if isinstance(enum, list):
        base.enum = [_data(base, member) for member in enum]
    examples = contents.get('examples')
    if isinstance(examples, list) and examples:
        base.examples = Examples(
            location=base.location,
            values={
                str(index): Example(
                    id=base._raml.next_id(),  # noqa: SLF001 - one counter per parse
                    name=str(index),
                    location=base.location,
                    data=_data(base, value),
                )
                for index, value in enumerate(examples)
            },
        )
    return base


def _data(base: BaseShape, value: Any) -> DataNode:
    return DataNode(value=value_node_of(value), location=base.location)


def _int_facet(base: BaseShape, value: Any) -> ScalarFacet[int] | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return ScalarFacet(value=value, location=base.location)


def _fraction_facet(base: BaseShape, value: Any) -> ScalarFacet[Fraction] | None:
    """A bound as an exact `Fraction`, never through `float`.

    `json.loads` already made a `float` of `1.1`, so the conversion goes through
    its decimal text: `Fraction(repr(v))` recovers `11/10`, while the binary
    ratio would not divide evenly by anything the author wrote (docs/10 § 5.3).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return ScalarFacet(
        value=Fraction(value) if isinstance(value, int) else Fraction(repr(value)), location=base.location
    )


def _pattern_facet(base: BaseShape, value: Any) -> ScalarFacet[re.Pattern[str]] | None:
    if not isinstance(value, str):
        return None
    try:
        compiled = regex_engine(base._raml).compile(value)  # noqa: SLF001 - the parse's engine (docs/01 D3)
    except ImportError:
        raise
    except Exception:  # noqa: BLE001 - whatever the selected engine raises
        # A pattern the schema library accepts under ECMA-262 semantics may not
        # compile here, and `re2` rejects strictly more than `re` does. The
        # projection is a view, so the constraint is dropped rather than the
        # whole shape refused; `validate()` still enforces it.
        return None
    return ScalarFacet(value=compiled, location=base.location)


def _compile(context: _Projection, text: str) -> re.Pattern[str]:
    engine = regex_engine(context.parent._raml)  # noqa: SLF001 - as above
    try:
        compiled: re.Pattern[str] = engine.compile(text)
    except Exception as err:
        raise _unsupported(context, f'patternProperties: {text}') from err
    return compiled


def _attach(base: BaseShape, kind: str, shape: Shape) -> BaseShape:
    base.type = kind
    base.shape = shape
    return base


def _kind(base: BaseShape, kind: str, cls: Any, **built: Any) -> BaseShape:
    return _attach(base, kind, cls(base, **built))


# -- one self-contained schema -------------------------------------------------

#: Where a pulled-in reference is hung. `definitions` rather than `$defs`: every
#: draft understands it as a place to put subschemas, and a `$ref` into it is
#: the same pointer in all of them.
_BUNDLE_KEY = 'definitions'


@dataclass(frozen=True, slots=True)
class _Bundling:
    """What every level of the walk shares: where to resolve from, and into."""

    resolver: Resolver[Any]
    #: Name -> the pulled-in subschema, in encounter order.
    pulled: dict[str, Any]
    #: The identity of a resolved document -> the name it was given, so a second
    #: reference to it points at the first copy and a cycle terminates.
    named: dict[int, str]
    #: Every name in use, including the ones the document already had.
    taken: set[str]
    #: True while walking the document the bundle is *of*. A pointer there is a
    #: pointer into the result and stands. Inside anything pulled in it is a
    #: pointer into the file that was pulled, which the result is not: left
    #: alone it names whatever the result happens to have at that path, and a
    #: schema that validates something else is worse than one that is opaque.
    root: bool

    def at(self, resolver: Resolver[Any]) -> _Bundling:
        return _Bundling(resolver, self.pulled, self.named, self.taken, root=False)


def _bundle(compiled: CompiledSchema) -> Any:
    """`compiled`'s document with every reference out of it pulled in."""
    document = compiled.contents
    taken = (
        set(document[_BUNDLE_KEY])
        if isinstance(document, dict) and isinstance(document.get(_BUNDLE_KEY), dict)
        else set()
    )
    context = _Bundling(compiled.resolver, {}, {}, taken, root=True)
    bundled = _bundle_node(context, document)
    if not context.pulled or not isinstance(bundled, dict):
        return bundled
    existing = bundled.get(_BUNDLE_KEY)
    merged = {**existing, **context.pulled} if isinstance(existing, dict) else context.pulled
    return {**bundled, _BUNDLE_KEY: merged}


def _bundle_node(context: _Bundling, node: Any) -> Any:
    """`node` rebuilt, with each reference out of the document rewritten local.

    Rebuilt and not edited: the documents being walked are the registry's, shared
    with the validator and with every other type that names the same schema.
    """
    if isinstance(node, list):
        return [_bundle_node(context, item) for item in node]
    if not isinstance(node, dict):
        return node
    reference = node.get('$ref')
    if not isinstance(reference, str) or (context.root and reference.startswith('#')):
        return {key: _bundle_node(context, value) for key, value in node.items()}
    name = _pull(context, reference)
    if name is None:
        return {key: _bundle_node(context, value) for key, value in node.items()}
    # `$ref` first, where the author wrote it, and its siblings after: draft 2019
    # onward gives a schema beside a `$ref` meaning, so they are not dropped.
    rest = {key: _bundle_node(context, value) for key, value in node.items() if key != '$ref'}
    return {'$ref': f'#/{_BUNDLE_KEY}/{name}', **rest}


def _pull(context: _Bundling, reference: str) -> str | None:
    """Resolve `reference`, register what it names, and answer with that name.

    `None` where it does not resolve, which leaves the reference as the author
    wrote it. `_prefetch` has already resolved every reference in the schema by
    the time anything here runs, so this is the arm that should not be reachable
    rather than a fallback that is expected to fire.
    """
    from referencing.exceptions import Unresolvable  # noqa: PLC0415 - deferred for startup cost

    try:
        resolved = context.resolver.lookup(reference)
    except Unresolvable:
        return None
    known = context.named.get(id(resolved.contents))
    if known is not None:
        return known
    name = _bundle_name(reference, context.taken)
    # Registered before the walk into it, so a reference that leads back here
    # finds the name rather than descending again.
    context.named[id(resolved.contents)] = name
    context.pulled[name] = None
    context.pulled[name] = _bundle_node(context.at(resolved.resolver), resolved.contents)
    return name


def _bundle_name(reference: str, taken: set[str]) -> str:
    """A local name for what `reference` points at, unique within the document.

    The pointer's last segment where it has one, so `money.json#/definitions/
    Amount` stays `Amount`; otherwise the file's own stem.
    """
    stem = _definition_name(reference) or uri_base(reference.partition('#')[0]).rsplit('.', 1)[0] or 'schema'
    name = stem
    at = 2
    while name in taken:
        name = f'{stem}{at}'
        at += 1
    taken.add(name)
    return name

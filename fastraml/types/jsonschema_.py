"""External JSON Schema types — `JsonShape` and the registry behind it.

docs/10-validation.md § 7. A RAML type whose `type:` is a JSON document
rather than a type name delegates both halves of validation to a compiled JSON
Schema: `check()` is done at construction, and `validate()` runs the instance
against the compiled validator.

One `SchemaRegistry` per parse, so a `$ref` target shared by forty schemas is
read and parsed once. Every read goes through `Raml.loader`, which
is what keeps the workspace sandbox and the remote-includes switch in force: a
`$ref` to `http://json-schema.org/...` in an offline parse fails loudly rather
than reaching the network.

`JsonShape` lives here rather than beside the other structured kinds because
compiling a schema needs `referencing` and the loader, and the RAML projection
needs to build object, array and union shapes, so this module sits above
`complex_.py` and `scalars.py` and is imported by `shape.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urldefrag, urljoin

from fastraml.datanode import DataNode, value_node_of
from fastraml.errors import ErrorKind, RamlError
from fastraml.parser.facets import regex_engine
from fastraml.types.base import (
    TYPE_ANY,
    TYPE_ARRAY,
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
from fastraml.types.complex_ import ArrayShape, ComplexKind, ObjectShape, RecursiveShape, UnionShape
from fastraml.types.examples import Example, Examples
from fastraml.types.scalars import AnyShape
from fastraml.types.values import EnumValues, index_path, key_path, rejected
from fastraml.uris import uri_stem
from fastraml.yamlnode import node_error

if TYPE_CHECKING:
    # Annotations only: every pattern is compiled through `regex_engine`, so
    # the parse's engine applies to projected patterns too (docs/01 § 4.2).
    import re

    from referencing import Registry, Resource
    from referencing._core import Resolver

    from fastraml.positions import Position
    from fastraml.registry import Raml
    from fastraml.types.base import Shape
    from fastraml.yamlnode import Node

__all__ = [
    'CompiledSchema',
    'JsonShape',
    'SchemaRegistry',
    'escape_json_pointer_segment',
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

    The validator alone would do for `validate()`, but the projection walks the schema
    document itself and follows its `$ref`s, and both of those live behind
    private attributes of the validator.
    """

    validator: Any
    contents: Any
    resolver: Resolver[Any]
    #: The compiled subschema's canonical URI, document plus JSON Pointer. Its
    #: identity, and the key its projection is shared under.
    uri: str


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
    """The JSON Schema resources of one parse (docs/10 § 7).

    Held on `Raml`, built on first use. The cache is what makes a shared `$ref`
    target linear rather than quadratic: `referencing`'s own registry is a
    persistent structure whose retrievals are discarded with the copy that made
    them, so the memo has to live here.
    """

    __slots__ = ('_projections', '_raml', '_reached', '_resources')

    def __init__(self, raml: Raml) -> None:
        self._raml = raml
        self._resources: dict[str, Resource[Any]] = {}
        #: The documents the schema being compiled reached, by URI.
        self._reached: set[str] = set()
        #: Projections by the subschema's canonical URI, with the named
        #: definitions a walk of the whole document collected.
        #:
        #: One entry per URI, however the walk reached it: a schema file reached
        #: as the RAML type that `!include`d it and as another schema's `$ref`
        #: target is one subschema, so it is one shape (and one view address).
        self._projections: dict[str, tuple[BaseShape, dict[str, BaseShape]]] = {}

    def projected(self, uri: str) -> tuple[BaseShape, dict[str, BaseShape]] | None:
        return self._projections.get(uri)

    def share(self, uri: str, built: BaseShape, defs: dict[str, BaseShape] | None = None) -> None:
        """Record a projection. `defs` only where a whole document was walked."""
        self._projections[uri] = (built, defs if defs is not None else {})

    def compile(self, raw: str, location: str, position: Position | None) -> CompiledSchema:
        """Compile one schema, resolving every reference it names.

        `location` may carry a JSON Pointer (`schema.json#/definitions/User`),
        which selects the subschema the RAML type stands for; the whole document
        is still the compilation unit, so a sibling definition it references
        resolves.
        """
        # JSON Schema is uncommon in ordinary RAML documents. Keep its sizeable
        # dependency tree off the startup path until a schema is actually used.
        from jsonschema import FormatChecker  # noqa: PLC0415 - deferred for startup cost
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
                info={'keyword': str(err.validator), 'path': '/'.join(str(part) for part in err.absolute_path)},
            ) from err

        entry = Resource.from_contents(contents, default_specification=specification)
        if _is_one_schema(self._raml, document_uri):
            # One document, one resource, per parse: a `$ref` back into this
            # file is then this very document, which the bundle and the
            # projection recognise by identity. An inline schema is not the
            # document it is written in, and stays out.
            entry = self._resources.setdefault(document_uri, entry)
            contents = entry.contents
        # `retrieve` is the init alias of the private `_retrieve` field, which
        # mypy's attrs plugin does not derive. Crawled now: a lookup that
        # misses crawls whatever is uncrawled, so an uncrawled entry would be
        # crawled again for every document it names.
        registry: Registry[Any] = (
            Registry(retrieve=self._retrieve).with_resource(document_uri, entry).crawl()  # type: ignore[call-arg]
        )
        self._reached = set()
        selected, resolver = self._select(registry, document_uri, pointer, contents, location, position)
        self._prefetch(selected, resolver, specification, location, position, set())
        # The schema's own registry: every document it reaches, crawled once.
        # `referencing` keeps what a lookup retrieves only in the registry that
        # lookup returns, so on the registry above the validator and both views
        # would re-crawl and re-retrieve each other document at every `$ref`.
        registry = registry.with_resources((uri, self._resources[uri]) for uri in self._reached).crawl()
        contents, resolver = self._select(registry, document_uri, pointer, contents, location, position)
        return CompiledSchema(
            validator=validator_class(contents, registry=registry, _resolver=resolver, format_checker=FormatChecker()),
            contents=contents,
            resolver=resolver,
            uri=f'{_document_of(resolver) or document_uri}#{pointer}',
        )

    def _select(  # noqa: PLR0913, PLR0917 - a lookup with its diagnostic context
        self,
        registry: Registry[Any],
        document_uri: str,
        pointer: str,
        contents: Any,
        location: str,
        position: Position | None,
    ) -> tuple[Any, Resolver[Any]]:
        """The subschema `pointer` selects in the document, and its resolver."""
        resolver = registry.resolver(document_uri)
        if not pointer:
            return contents, resolver
        resolved = self._lookup(resolver, '#' + pointer, location, position)
        return resolved.contents, resolved.resolver

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
        inside the schema library, `_prefetch` here, and the RAML projection —
        and the first of them is not ours to guard from the inside. A 200-level
        schema exhausts CPython's stack inside `jsonschema`'s meta-schema
        validation and surfaces as `RecursionError`, which docs/12 § 3
        forbids. Measuring the depth first is one iterative pass over a
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
        """`referencing`'s hook: every `$ref` target is read through the loader,
        once per parse, and recorded as reached by the schema being compiled.
        """
        resource = self._resources.get(uri) or self._load(uri)
        self._reached.add(uri)
        return resource

    def _load(self, uri: str) -> Resource[Any]:
        from referencing import Resource  # noqa: PLC0415 - deferred for startup cost
        from referencing.jsonschema import DRAFT7  # noqa: PLC0415

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
    """The draft a schema declares, or 7 — what go-raml assumes."""
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
    runtime (docs/02-architecture.md § 2).
    """
    existing = raml.json_schema_registry
    if existing is None:
        existing = SchemaRegistry(raml)
        raml.json_schema_registry = existing
    return existing


class JsonShape(ComplexKind):
    """A type declared by an external or inline JSON Schema.

    RAML sibling facets that reach this kind are rejected. Common facets are
    removed by `shape.py` first and are currently accepted by the parser; see
    docs/10-validation.md § 7 for the exact behavior. Inheritance can
    merge only an identical schema.

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
            # The failing instance's path, in RAML's spelling, so the value is
            # found in the example (docs/11 § 3).
            for part in err.absolute_path:
                path = index_path(path, part) if isinstance(part, int) else key_path(path, part)
            raise rejected(
                'value does not match the JSON schema',
                self.base,
                info={
                    'path': path,
                    'schema_path': '/'.join(str(part) for part in err.absolute_schema_path),
                },
            ) from err
        except Unresolvable as err:
            raise SchemaRegistry._reference_error(  # noqa: SLF001 - one diagnostic, two call sites
                err, str(getattr(err, 'ref', '')), self.base.location, self.base.value_pos
            ) from err

    # -- the projection (docs/10 § 7) -----------------------------------------

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
            registry = schema_registry(self.base._raml)  # noqa: SLF001 - one registry per parse
            uri = self.canonical_uri
            shared = registry.projected(uri) if uri else None
            if shared is None:
                defs: dict[str, BaseShape] = {}
                _, _, pointer = (uri or self._compiled.uri).partition('#')
                built = _project(
                    _Projection(self.base, self._compiled.resolver, defs, pointer), self._compiled.contents, {}
                )
                if uri:
                    registry.share(uri, built, defs)
                shared = (built, defs)
            self._cached_shape, self._cached_defs = shared
        return self._cached_shape

    def as_shape_defs(self) -> dict[str, BaseShape] | None:
        """The named `$ref` targets `as_shape` extracted, in encounter order.

        `None` until `as_shape` has run — the two are one traversal.
        """
        return self._cached_defs

    def as_shape_definitions(self) -> dict[str, BaseShape]:
        """Named reference targets and every top-level definition, including unused ones.

        `as_shape_defs()` is the traversal's encountered references; an export
        also needs definitions the root did not reference. Project those only
        when requested, without changing the cached projection or parser model.
        """
        root = self.as_shape()
        if self._compiled is None:
            return {}
        defs = dict(self._cached_defs or {})
        contents = self._compiled.contents
        declared: dict[str, BaseShape] = {}
        if isinstance(contents, dict):
            _, _, pointer = self._compiled.uri.partition('#')
            context = _Projection(self.base, self._compiled.resolver, defs, pointer)
            for keyword, entries in contents.items():
                if keyword not in {'definitions', '$defs'}:
                    continue
                if not isinstance(entries, dict):
                    continue
                for name in entries:
                    reference = f'#/{keyword}/{escape_json_pointer_segment(name)}'
                    candidate, suffix = name, 2
                    while candidate in declared:
                        candidate = f'{name}{suffix}'
                        suffix += 1
                    declared[candidate] = _project_reference(context, reference, {})
        # A local alias to `node.json` can register the same shape under both
        # `Node` (the author's definition) and `node` (the file stem). The
        # declared name wins; otherwise recursive references would name the
        # file stem without a corresponding Library declaration.
        result = dict(declared)
        used = set(result.values())
        if root is not None:
            _collect_projection_names(root, result, used, declared, defs)
        for name, base in defs.items():
            _claim_projection_name(result, used, name, base)
        return result

    @property
    def canonical_uri(self) -> str | None:
        """The subschema's identity, or `None` for a schema written inline.

        An inline schema compiles under the RAML file's own URI, which it shares
        with every other inline schema in that file — so it has no identity to
        be shared under, and two of them would collapse into one projection.
        """
        if self._compiled is None or not self.document_uri:
            return None
        if not _is_one_schema(self.base._raml, self.document_uri):  # noqa: SLF001 - the parse's index
            return None
        return self._compiled.uri

    @property
    def contents(self) -> Any | None:
        """The parsed schema selected by this declaration, without copying.

        This is the same object the validator and RAML-shape projection read.
        Unlike `as_schema()`, it does not bundle or rewrite external references.
        Syntax-aware views may inspect it but must not mutate it.
        """
        return self._compiled.contents if self._compiled is not None else None

    @property
    def document_uri(self) -> str | None:
        """The resolver's base document, including the RAML file for inline schemas.

        Not `base.location`, which for `type: !include person.json` is the RAML
        file. The resolver is re-based onto the document it retrieved. For an
        inline schema this is the RAML file, not a schema identity; use
        `canonical_uri` to distinguish it from a schema file.
        """
        if self._compiled is None:
            return None
        return _document_of(self._compiled.resolver)

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
            self._cached_schema = _bundle(self._compiled, self.canonical_uri)
        return self._cached_schema


def _claim_projection_name(found: dict[str, BaseShape], used: set[BaseShape], name: str, base: BaseShape) -> None:
    if base in used:
        return
    candidate, suffix = name, 2
    while candidate in found:
        candidate = f'{name}{suffix}'
        suffix += 1
    found[candidate] = base
    used.add(base)


def _collect_projection_names(
    root: BaseShape,
    found: dict[str, BaseShape],
    used: set[BaseShape],
    declared: dict[str, BaseShape],
    defs: dict[str, BaseShape],
) -> None:
    """Find named targets inside cached subtrees that did not re-enter the walk."""
    stack = list(reversed([root, *declared.values(), *defs.values()]))
    seen: set[BaseShape] = set()
    while stack:
        base = stack.pop()
        if base in seen:
            continue
        seen.add(base)
        if (
            base is not root
            and base.location != root.location
            and base.name
            and '#' in base.location
            and not isinstance(base.shape, RecursiveShape)
        ):
            _claim_projection_name(found, used, base.name, base)
        shape = base.shape
        if isinstance(shape, RecursiveShape):
            stack.append(shape.head)
        elif isinstance(shape, ObjectShape):
            stack.extend(reversed([prop.base for prop in (shape.properties or {}).values()]))
            stack.extend(reversed([prop.base for prop in (shape.pattern_properties or {}).values()]))
        elif isinstance(shape, ArrayShape) and shape.items is not None:
            stack.append(shape.items)
        elif isinstance(shape, UnionShape):
            stack.extend(reversed(shape.any_of or ()))


def projected(base: BaseShape) -> BaseShape:
    """`base` as a consumer walking structure should see it.

    A JSON-schema type through its RAML projection, anything else unchanged.
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


def subschema_document(base: BaseShape) -> str | None:
    """The schema document `base` is a subschema of, or `None` if it is not one.

    A view shape's `location` is its canonical URI, document plus JSON Pointer
    so the fragment separator is the test. A shape the
    RAML document declared carries a plain file URI.

    This is what makes `BaseShape.name` readable. That field holds a property key
    on a declared shape and a `definitions` key on a projected one, and only the
    second is a type name -- so a consumer printing it checks here first, or
    renders `currencyCode: currencyCode`.

    Read from the shape rather than from `as_shape_defs`, which a shared
    projection leaves incomplete: a walk served a subtree from the cache never
    re-enters it, so the names inside it are missing from *that* document's
    table.
    """
    document, separator, _ = base.location.partition('#')
    return document if separator else None


# -- JSON Schema -> the nearest RAML shape (docs/10 § 7) -----------------------


@dataclass(frozen=True, slots=True)
class _Projection:
    """What every level of the walk shares: where to hang the view shapes."""

    parent: BaseShape
    resolver: Resolver[Any]
    defs: dict[str, BaseShape]
    #: JSON Pointer of the subschema being walked, within `resolver`'s document.
    pointer: str = ''
    #: A containing object/array can recover from an impossible child restriction.
    allow_empty: bool = False

    def at(self, resolver: Resolver[Any], pointer: str) -> _Projection:
        """The same walk, moved into another document at `pointer`."""
        return _Projection(self.parent, resolver, self.defs, pointer, self.allow_empty)

    def into(self, *segments: str) -> _Projection:
        """One step deeper in the current document."""
        suffix = ''.join(f'/{escape_json_pointer_segment(segment)}' for segment in segments)
        return _Projection(self.parent, self.resolver, self.defs, self.pointer + suffix, self.allow_empty)


def escape_json_pointer_segment(segment: str) -> str:
    """One JSON Pointer segment, escaped per RFC 6901.

    `~` before `/`, or the second substitution rewrites the first. Written here
    because `referencing` only goes the other way, and inline: `Resource.pointer`
    unescapes as it walks and exposes nothing.
    """
    return segment.replace('~', '~0').replace('/', '~1')


def _unsupported(context: _Projection, what: str) -> RamlError:
    return RamlError.new(
        'JSON schema construct has no RAML equivalent',
        context.parent.location,
        context.parent.value_pos,
        kind=ErrorKind.RESOLVING,
        info={'construct': what},
    )


def _document_of(resolver: Resolver[Any]) -> str | None:
    """The document a resolver is based on. `lookup` re-bases onto its target."""
    return getattr(resolver, '_base_uri', None) or None


def _is_one_schema(raml: Raml, document: str | None) -> bool:
    """Whether `document` holds exactly this schema, so its URI identifies it.

    A `$ref` target is read by `SchemaRegistry` and is not a fragment at all. An
    `!include`d schema is wrapped into a one-type `DataTypeFragment`. Either way
    the file is the schema, and its URI is an identity a projection can be
    shared under.

    An API or a library is not: every schema written inline in it compiles under
    that one URI, so sharing on it makes the second inline schema in a file
    answer with the first one's projection.
    """
    from fastraml.parser.fragments import DataTypeFragment  # noqa: PLC0415 - avoids a cycle with `parser`

    if document is None:
        return False
    fragment = raml.fragments.get(document)
    return fragment is None or isinstance(fragment, DataTypeFragment)


def _subschema_uri(context: _Projection, document: str | None) -> str | None:
    """The canonical URI of the subschema being walked, or `None`.

    Document plus JSON Pointer, so `location` *is* the identity — one field, and
    a URI with a fragment, which `location` already carries for a RAML type
    declared from `schema.json#/definitions/User`.

    `None` where the document is not the schema: every schema written inline in
    an API or a library compiles under that one URI, so it identifies none of
    them. The absence of a `#` is then what tells a consumer to fall back to
    addressing by containment.
    """
    if not _is_one_schema(context.parent._raml, document):  # noqa: SLF001 - the parse's index
        return None
    return f'{document}#{context.pointer}'


def _view_base(context: _Projection, name: str | None = None) -> BaseShape:
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
    document = _document_of(context.resolver)
    location = _subschema_uri(context, document)
    base = BaseShape(
        id=context.parent._raml.next_id(),  # noqa: SLF001 - one counter per parse (docs/02 § 3)
        raml=context.parent._raml,  # noqa: SLF001 - as above
        location=location or context.parent.location,
        name=name or (_subschema_name(location) if location else None),
    )
    base._unwrapped = True  # noqa: SLF001 - a view shape has nothing left to flatten
    return base


type _Visiting = dict[int, BaseShape | None]


def _project(context: _Projection, contents: Any, visiting: _Visiting) -> BaseShape:
    """One schema node, projected onto the nearest RAML shape (docs/10 § 7)."""
    # `visiting` holds one entry per level currently open — it is added to
    # before descending and removed in a `finally` — so its size *is* the depth,
    # and the guard costs a `len`. Each document was already bounded by
    # `_check_nesting`; what this catches is a chain of `$ref`s across many
    # shallow documents, which nests as deep as the chain is long.
    if len(visiting) > context.parent._raml.max_depth:  # noqa: SLF001 - the parse's ceiling (docs/12 § 3)
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
    siblings_apply = False
    if isinstance(reference, str):
        from fastraml.types.schema_intersection import ref_siblings_apply  # noqa: PLC0415 - shared draft policy

        siblings_apply = ref_siblings_apply(context, contents)
    key = id(contents)
    present, previous = key in visiting, visiting.get(key)
    # A productive recursive target may revisit its caller's reference wrapper.
    # Restore that frame on return; it is not owned by the nested traversal.
    base = None if isinstance(reference, str) and not siblings_apply else _decorate(_view_base(context), contents)
    visiting[key] = base
    try:
        if isinstance(reference, str) and not siblings_apply:
            return _project_reference(context, reference, visiting)
        assert base is not None  # noqa: S101 - only reference-only frames have no type head
        if siblings_apply:
            return _project_all_of(context, contents, base, visiting)
        if 'if' in contents:
            raise _unsupported(context, 'if/then/else')
        return _project_body(context, contents, base, visiting)
    finally:
        if present:
            visiting[key] = previous
        else:
            del visiting[key]


def _project_reference(context: _Projection, reference: str, visiting: _Visiting) -> BaseShape:
    resolved = context.resolver.lookup(reference)
    # Where the reference lands, split the way `Resolver.lookup` splits it. A
    # reference moves the walk outright rather than deeper, so this replaces the
    # current position instead of extending it. `urljoin` needs no special case
    # for a bare `#...`: it appends the fragment to the base, which is what
    # `lookup` shortcuts to.
    document, target = urldefrag(urljoin(_document_of(context.resolver) or '', reference))
    if id(resolved.contents) in visiting:
        head = visiting[id(resolved.contents)]
        if head is None:
            raise _unsupported(context, 'reference-only cycle')
        # The back-edge of a cycle, which is exactly what P9 produces for a
        # recursive RAML type (docs/07 § 6).
        base = _view_base(context, head.name)
        base.type = TYPE_RECURSIVE
        base.shape = RecursiveShape(base, head)
        return base

    uri = f'{document}#{target}'
    # Named by the rule `_view_base` uses, so a target reached through a `$ref`
    # and the same target reached by descent agree. A reference with no pointer
    # names the whole document, which `_view_base` already calls after its file.
    name = _subschema_name(uri) if target else None
    if name is not None:
        existing = context.defs.get(name)
        if existing is not None and existing.location == uri:
            return existing
    # Only a target in a file of its own has a canonical URI to share under. An
    # inline schema compiles under the RAML file's URI, which it shares with
    # every other inline schema in that file.
    canonical = uri if _is_one_schema(context.parent._raml, document) else None  # noqa: SLF001 - the parse's index
    registry = schema_registry(context.parent._raml)  # noqa: SLF001 - one registry per parse
    shared = registry.projected(canonical) if canonical else None
    if shared is not None:
        if name is not None:
            context.defs[name] = shared[0]
        return shared[0]

    built = _project(context.at(resolved.resolver, target), resolved.contents, visiting)
    if name is not None:
        built.name = name
        context.defs[name] = built
    if canonical is not None:
        # After the walk, never during it: a shape still being built is one a
        # cycle must reach through `visiting`.
        registry.share(canonical, built)
    return built


def _subschema_name(location: str) -> str | None:
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


def _pointer_tail(reference: str) -> str | None:
    """The last segment of a reference's JSON Pointer, if it has one.

    A *key*, not a type name: `_bundle_name` wants something short and unique
    per document. `_subschema_name` is the one that decides what a subschema is
    called, and it names only the forms a `$ref` can address.
    """
    pointer = reference.partition('#')[2]
    if not pointer.startswith('/'):
        return None
    segment = pointer.rsplit('/', 1)[-1]
    return segment or None


def _project_body(context: _Projection, contents: dict, base: BaseShape, visiting: _Visiting) -> BaseShape:
    if contents.get('allOf'):
        return _project_all_of(context, contents, base, visiting)
    from fastraml.types.schema_intersection import _STRING_FORMATS, intersect  # noqa: PLC0415 - shared reducers

    for keyword in ('oneOf', 'anyOf'):
        # `oneOf`'s exactly-one semantics is lost. RAML's union is "at least
        # one" and there is nothing nearer; docs/10 § 7 records the loss.
        members = contents.get(keyword)
        if members:
            if contents.get('format') in _STRING_FORMATS:
                # A format beside a disjunction constrains the complete result;
                # the ordinary union projector does not distribute sibling facets.
                raise _unsupported(context, f'{keyword} with format')
            return _project_union(context, keyword, members, base, visiting)

    declared = contents.get('type') or _inferred_type(contents)
    if declared is None and not contents.keys() & {'enum', 'const'} and contents.get('format') not in _STRING_FORMATS:
        return _kind(base, TYPE_ANY, AnyShape)
    if declared == 'object' and 'patternProperties' in contents:
        return _project_object(context, contents, base, visiting)
    return intersect(context, contents, base, visiting, strict=False)


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

    The projection has to pick a kind, because RAML has no way to spell
    "a constraint that applies only to objects, and is silent otherwise". When
    every keyword present points at one kind, that kind is the least-lossy pick.

    When they point at more than one — `{"properties": {...}, "minLength": 3}`
    constrains objects *and* strings, which is legal and which RAML cannot
    express — this returns `None` and the shape stays `any` rather than silently
    choosing. Losing the constraints is bad; claiming the wrong kind is worse.

    `allOf` selects a type from the whole intersection before projecting its
    conditional constraints (docs/10 § 7).
    """
    implied = {_KEYWORD_TYPE[keyword] for keyword in contents if keyword in _KEYWORD_TYPE}
    return implied.pop() if len(implied) == 1 else None


def _project_all_of(context: _Projection, contents: dict, base: BaseShape, visiting: _Visiting) -> BaseShape:
    """Intersect source constraints rather than directional RAML inheritance."""
    from fastraml.types.schema_intersection import intersect  # noqa: PLC0415 - used only by JSON Schema projection

    return intersect(context, contents, base, visiting)


def _project_union(
    context: _Projection, keyword: str, members: list, base: BaseShape, visiting: _Visiting
) -> BaseShape:
    projected = [_project(context.into(keyword, str(i)), member, visiting) for i, member in enumerate(members)]
    return _kind(base, TYPE_UNION, UnionShape, any_of=projected)


def _project_object(context: _Projection, contents: dict, base: BaseShape, visiting: _Visiting) -> BaseShape:
    extras = contents.get('additionalProperties')
    if isinstance(extras, dict):
        raise _unsupported(context, 'schema-form additionalProperties')

    required = set(contents.get('required') or ())
    properties = {
        name: Property(
            name=name, base=_project(context.into('properties', name), schema, visiting), required=name in required
        )
        for name, schema in (contents.get('properties') or {}).items()
    }
    patterns: dict[str, PatternProperty] = {}
    for text, schema in (contents.get('patternProperties') or {}).items():
        compiled = _compile(context, text)
        patterns[f'/{text}/'] = PatternProperty(
            pattern=compiled, base=_project(context.into('patternProperties', text), schema, visiting)
        )

    shape = ObjectShape(base, properties=properties or None, pattern_properties=patterns or None)
    shape.min_properties = _int_facet(base, contents.get('minProperties'))
    shape.max_properties = _int_facet(base, contents.get('maxProperties'))
    if isinstance(extras, bool):
        shape.additional_properties = ScalarFacet(value=extras, location=base.location)
    return _attach(base, TYPE_OBJECT, shape)


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
        base.enum = EnumValues(_data(base, member) for member in enum)
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


def _pattern_facet(base: BaseShape, value: Any) -> ScalarFacet[re.Pattern[str]] | None:
    if not isinstance(value, str):
        return None
    try:
        compiled = regex_engine(base._raml).compile(value)  # noqa: SLF001 - the parse's engine (docs/01 § 4.2)
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
    #: The identity of a resolved schema -> the local reference that stands for
    #: it, so a second reference points at the first copy, a cycle terminates,
    #: and the bundled root answers `#`.
    named: dict[int, str]
    #: Every name in use, including the ones the document already had.
    taken: set[str]
    #: True while walking a whole document the bundle is *of*. A pointer there
    #: is a pointer into the result and stands. Inside anything pulled in, or
    #: in a subschema bundled on its own, it is a pointer into a file the
    #: result is not: left alone it names whatever the result happens to have
    #: at that path, and a schema that validates something else is worse than
    #: one that is opaque.
    root: bool
    #: The file the bundle is of, where it is a whole file of its own: a
    #: reference back into it, from anywhere, is a pointer into the result.
    document: str | None = None
    #: The bundled location of this document, for aliases in its definitions.
    slot: str = '#'

    def at(self, resolver: Resolver[Any], slot: str) -> _Bundling:
        return _Bundling(resolver, self.pulled, self.named, self.taken, root=False, document=self.document, slot=slot)


def _bundle(compiled: CompiledSchema, canonical: str | None) -> Any:
    """`compiled`'s document with every reference out of it pulled in.

    `canonical` is the schema's identity (`JsonShape.canonical_uri`); without
    a pointer it names the whole file, which references back into it then
    point into rather than copy.
    """
    document = compiled.contents
    taken = (
        set(document[_BUNDLE_KEY])
        if isinstance(document, dict) and isinstance(document.get(_BUNDLE_KEY), dict)
        else set()
    )
    whole, _, pointer = (canonical or '').partition('#')
    selected = compiled.uri.partition('#')[2]
    context = _Bundling(
        compiled.resolver,
        {},
        {id(document): '#'},
        taken,
        root=not selected,
        document=None if pointer else whole or None,
    )
    bundled = _bundle_root(context, document)
    if not context.pulled or not isinstance(bundled, dict):
        return bundled
    existing = bundled.get(_BUNDLE_KEY)
    merged = {**existing, **context.pulled} if isinstance(existing, dict) else context.pulled
    return {**bundled, _BUNDLE_KEY: merged}


def _definition_aliases(context: _Bundling, document: Any) -> dict[str, Any]:
    """Claim exact external aliases at the document's bundled location.

    Without this first pass, `definitions: {uuid: {$ref: "uuid.json"}}`
    reserves `uuid`, then the ordinary pull has to call the target `uuid2`.
    Claim aliases in every document before walking its properties, including
    documents pulled into a definition of the entry schema.
    """
    from referencing.exceptions import Unresolvable  # noqa: PLC0415 - deferred for startup cost

    if not isinstance(document, dict) or not isinstance(document.get(_BUNDLE_KEY), dict):
        return {}
    aliases: dict[str, Any] = {}
    for name, node in document[_BUNDLE_KEY].items():
        if not isinstance(node, dict) or set(node) != {'$ref'}:
            continue
        reference = node.get('$ref')
        if not isinstance(reference, str) or reference.startswith('#'):
            continue
        try:
            resolved = context.resolver.lookup(reference)
        except Unresolvable:
            continue
        local = f'{context.slot}/{_BUNDLE_KEY}/{escape_json_pointer_segment(name)}'
        if id(resolved.contents) in context.named:
            # Another document already claimed the target. Keep this alias as
            # a reference to it, but local pointers must still find this slot.
            context.named[id(node)] = local
            continue
        context.named[id(resolved.contents)] = local
        # A local pointer resolves to the alias node, whereas a direct external
        # reference resolves to its target. Both already occupy this one slot.
        context.named[id(node)] = local
        aliases[name] = resolved
    return aliases


def _bundle_root(context: _Bundling, document: Any) -> Any:
    """Bundle one document, expanding its claimed definition aliases in place."""
    if not isinstance(document, dict):
        return _bundle_node(context, document)
    aliases = _definition_aliases(context, document)
    bundled: dict[str, Any] = {}
    for key, value in document.items():
        if key == _BUNDLE_KEY and isinstance(value, dict):
            bundled[key] = {
                name: _bundle_root(
                    context.at(
                        aliases[name].resolver, f'{context.slot}/{_BUNDLE_KEY}/{escape_json_pointer_segment(name)}'
                    ),
                    aliases[name].contents,
                )
                if name in aliases
                else _bundle_node(context, node)
                for name, node in value.items()
            }
        else:
            bundled[key] = _bundle_node(context, value)
    return bundled


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
    local = _pull(context, reference)
    if local is None:
        return {key: _bundle_node(context, value) for key, value in node.items()}
    # `$ref` first, where the author wrote it, and its siblings after: draft 2019
    # onward gives a schema beside a `$ref` meaning, so they are not dropped.
    rest = {key: _bundle_node(context, value) for key, value in node.items() if key != '$ref'}
    return {'$ref': local, **rest}


def _pull(context: _Bundling, reference: str) -> str | None:
    """Resolve `reference` and answer with the local reference that replaces
    it: a pointer into the result where it lands in the bundled file itself,
    else a `definitions` entry holding what it names, registered on first use.

    `None` where it does not resolve, which leaves the reference as the author
    wrote it. `_prefetch` has already resolved every reference in the schema by
    the time anything here runs, so this is the arm that should not be reachable
    rather than a fallback that is expected to fire.
    """
    from referencing.exceptions import Unresolvable  # noqa: PLC0415 - deferred for startup cost

    document, fragment = urldefrag(urljoin(_document_of(context.resolver) or '', reference))
    if document == context.document:
        return f'#{fragment}'
    try:
        resolved = context.resolver.lookup(reference)
    except Unresolvable:
        return None
    known = context.named.get(id(resolved.contents))
    if known is not None:
        return known
    name = _bundle_name(reference, context.taken)
    local = f'#/{_BUNDLE_KEY}/{name}'
    # Registered before the walk into it, so a reference that leads back here
    # finds the name rather than descending again.
    context.named[id(resolved.contents)] = local
    context.pulled[name] = None
    context.pulled[name] = _bundle_root(context.at(resolved.resolver, local), resolved.contents)
    return local


def _bundle_name(reference: str, taken: set[str]) -> str:
    """A local name for what `reference` points at, unique within the document.

    The pointer's last segment where it has one, so `money.json#/definitions/
    Amount` stays `Amount`; otherwise the file's own stem.
    """
    stem = _pointer_tail(reference) or uri_stem(reference.partition('#')[0]) or 'schema'
    name = stem
    at = 2
    while name in taken:
        name = f'{stem}{at}'
        at += 1
    taken.add(name)
    return name

"""Compiling a JSON Schema, and the registry of one parse's schema documents.

docs/10-validation.md § 7. A RAML type whose `type:` is a JSON document
rather than a type name delegates validation to a compiled JSON Schema; this
module compiles it. `SchemaRegistry` holds every schema document a parse reads,
so a `$ref` target shared by forty schemas is read and parsed once. Every read
goes through `Raml.loader`, which is what keeps the workspace sandbox and the
remote-includes switch in force: a `$ref` to `http://json-schema.org/...` in an
offline parse fails loudly rather than reaching the network.

Also here: the meta-schema check, the draft a schema is read in, the exact
`multipleOf` every compiled validator uses, and the document identity the
projection is shared under. It imports nothing else of the JSON Schema
modules.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urldefrag, urljoin

from fastraml.errors import ErrorKind, RamlError
from fastraml.records import record
from fastraml.types.values import as_fraction, is_multiple_of

if TYPE_CHECKING:
    from collections.abc import Iterator

    from referencing import Registry, Resource
    from referencing._core import Resolver

    from fastraml.positions import Position
    from fastraml.registry import Raml
    from fastraml.types.base import BaseShape


#: Keywords whose values are user data rather than subschemas. A `$ref` written
#: inside a `default` is a value that happens to look like a reference, and
#: resolving it would reject a document the spec allows.
DATA_KEYWORDS: Final = frozenset({'const', 'default', 'enum', 'example', 'examples'})


#: Keywords whose value is a *mapping of* subschemas. The mapping itself is not
#: a schema, so its keys must not be read as keywords.
SCHEMA_MAPS: Final = frozenset(
    {'$defs', 'definitions', 'dependencies', 'dependentSchemas', 'patternProperties', 'properties'}
)


@record
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

    __slots__ = ('_draft', '_failures', '_projections', '_raml', '_reached', '_resources')

    def __init__(self, raml: Raml) -> None:
        self._raml = raml
        self._resources: dict[str, Resource[Any]] = {}
        #: The documents the schema being compiled reached, by URI.
        self._reached: set[str] = set()
        #: The validator class of the schema being compiled: the draft a
        #: document it reaches is held to when that document names none.
        self._draft: Any = None
        #: Projections by the subschema's canonical URI, with the named
        #: definitions a walk of the whole document collected.
        #:
        #: One entry per URI and draft, however the walk reached it: a schema
        #: file reached as the RAML type that `!include`d it and as another
        #: schema's `$ref` target is one subschema, so it is one shape (and one
        #: view address). The draft is the entry schema's (`draft_of`): a
        #: document declaring none is read in it, so each draft is a reading of
        #: its own, computed once.
        self._projections: dict[tuple[str, str], tuple[BaseShape, dict[str, BaseShape]]] = {}
        #: Why a subschema projected on its own has no projection, keyed as
        #: `_projections` is. Read only by `as_shape`, after `_projections`.
        self._failures: dict[tuple[str, str], RamlError] = {}

    def projected(self, uri: str, draft: str) -> tuple[BaseShape, dict[str, BaseShape]] | None:
        return self._projections.get((uri, draft))

    def share(self, uri: str, draft: str, built: BaseShape, defs: dict[str, BaseShape] | None = None) -> None:
        """Record a projection. `defs` only where a whole document was walked."""
        self._projections[uri, draft] = (built, defs if defs is not None else {})

    def failed(self, uri: str, draft: str) -> RamlError | None:
        return self._failures.get((uri, draft))

    def fail(self, uri: str, draft: str, error: RamlError) -> None:
        """Record that the subschema at `uri`, projected on its own, has no projection."""
        self._failures[uri, draft] = error

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
        from jsonschema.validators import Draft7Validator, validator_for  # noqa: PLC0415
        from referencing import Registry, Resource  # noqa: PLC0415

        document_uri, _, pointer = location.partition('#')
        contents = self._decode(raw, location, position)
        specification = specification_of(contents)
        self._draft = validator_for(contents, default=Draft7Validator)
        validator_class = _exact_validator(self._draft)
        _check_schema(validator_class, contents, location, position)

        entry = Resource.from_contents(contents, default_specification=specification)
        if is_one_schema(self._raml, document_uri):
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
            uri=f'{document_of(resolver) or document_uri}#{pointer}',
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
                raise _too_deep(location, position, limit)
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
        from jsonschema.validators import Draft7Validator, validator_for  # noqa: PLC0415 - deferred for startup cost
        from referencing import Resource  # noqa: PLC0415
        from referencing.jsonschema import DRAFT7  # noqa: PLC0415

        raml = self._raml
        try:
            data, oversized = raml.load_bounded(uri)
        except OSError as err:
            raise _LoadFailure(
                RamlError.wrap('load JSON schema', err, uri, kind=ErrorKind.LOADING, info={'path': uri})
            ) from err
        if oversized:
            info = {'path': uri, 'limit': raml.max_include_size}
            raise _LoadFailure(RamlError.new('JSON schema exceeds size limit', uri, kind=ErrorKind.LOADING, info=info))
        try:
            # The same decode as the entry schema's, so a `$ref` target is held
            # to the nesting ceiling too: without that, a shallow schema could
            # point at a 500-level one and reach the stack anyway.
            contents = self._decode(data, uri)
            # Checked against its draft as the entry schema is: `referencing`
            # crawls it next, and a malformed subschema (`"properties": {"a": 7}`)
            # would fail inside that crawl as a bare `TypeError`. A document
            # naming no `$schema` is read in the compiling schema's draft, as
            # the validator reads it.
            draft = validator_for(contents, default=self._draft or Draft7Validator)
            _check_schema(draft, contents, uri, None)
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
            raise self.reference_error(err, ref, location, position) from err

    @staticmethod
    def reference_error(err: Exception, ref: str, location: str, position: Position | None) -> RamlError:
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
        data (`DATA_KEYWORDS`) and the ones whose values are maps of subschemas
        (`SCHEMA_MAPS`), which is enough to keep a user value that looks like a
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
            raise _too_deep(location, position, self._raml.max_depth)
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
            if key == '$ref' or key in DATA_KEYWORDS:
                continue
            if key in SCHEMA_MAPS and isinstance(value, dict):
                for member in value.values():
                    self._prefetch(member, resolver, specification, location, position, seen, depth + 1)
            else:
                self._prefetch(value, resolver, specification, location, position, seen, depth + 1)


def _too_deep(location: str, position: Position | None, limit: int) -> RamlError:
    """A schema document, or a `$ref` chain through several, nested past the ceiling (docs/12 § 3)."""
    return RamlError.new(
        'JSON schema nesting too deep', location, position, kind=ErrorKind.PARSING, info={'limit': limit}
    )


def _check_schema(validator_class: Any, contents: Any, location: str, position: Position | None) -> None:
    """Refuse a schema document its draft's meta-schema rejects: `invalid JSON schema`."""
    from jsonschema.exceptions import SchemaError  # noqa: PLC0415 - deferred for startup cost

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


def specification_of(contents: Any) -> Any:
    """The draft a schema declares, or 7 — what go-raml assumes."""
    from referencing.jsonschema import DRAFT7, specification_with  # noqa: PLC0415 - deferred for startup cost

    declared = contents.get('$schema') if isinstance(contents, dict) else None
    if not isinstance(declared, str):
        return DRAFT7
    try:
        return specification_with(declared)
    except Exception:  # noqa: BLE001 - an unknown draft is not fatal; the meta-schema check reports it
        return DRAFT7


def draft_of(validator: Any) -> str:
    """The draft a compiled schema reads a document declaring none in.

    Part of a shared projection's key: the same file is a different reading
    under another entry's draft (docs/10 § 7).
    """
    validator_class: Any = type(validator)
    return _draft_named(validator_class)


@cache
def _draft_named(validator_class: Any) -> str:
    return str(specification_of(validator_class.META_SCHEMA).name)


#: Marks a class `_exact_validator` built, so `evolve` does not wrap it again.
_EXACT: Final = '_fastraml_exact'


@cache
def _exact_validator(validator_class: Any) -> Any:
    """`validator_class` with `multipleOf` checked exactly (docs/10 § 7).

    `jsonschema` divides floats, so `multipleOf: 0.1` rejects `0.7`. Replaced
    only in a draft that has the keyword; one class per draft.

    `evolve`, which every descent into a subschema calls, picks the class anew
    from the subschema's own `$schema`, so a `$ref` into a document of another
    draft would get the stock class back. The class's `evolve` hands that
    draft's exact class on instead.
    """
    from jsonschema.validators import extend  # noqa: PLC0415 - deferred for startup cost

    keywords = {'multipleOf': _exact_multiple_of} if 'multipleOf' in validator_class.VALIDATORS else {}
    exact = extend(validator_class, keywords)
    setattr(exact, _EXACT, True)
    stock_evolve = exact.evolve

    def evolve(self: Any, **changes: Any) -> Any:
        evolved = stock_evolve(self, **changes)
        picked: Any = type(evolved)
        if getattr(picked, _EXACT, False):
            return evolved
        # The same rebuild as jsonschema's own `evolve`, which reads the fields
        # through `attrs.fields`. That returns `__attrs_attrs__`; read here
        # directly because `attrs` is jsonschema's dependency, not a declared
        # one of fastraml's.
        return _exact_validator(picked)(
            **{field.alias: getattr(evolved, field.name) for field in picked.__attrs_attrs__ if field.init}
        )

    exact.evolve = evolve
    return exact


def _exact_multiple_of(validator: Any, divisor: Any, instance: Any, _schema: Any) -> Iterator[Any]:
    """`jsonschema`'s `multipleOf`, with both numbers read as `as_fraction` reads them.

    Never through `float`: `0.7` and `0.1` are the fractions their text names,
    as for a RAML `multipleOf` (docs/10 § 5), and through the same check. An
    infinite instance is a multiple of nothing.
    """
    from jsonschema.exceptions import ValidationError  # noqa: PLC0415 - deferred for startup cost

    if not validator.is_type(instance, 'number'):
        return
    value, step = as_fraction(instance), as_fraction(divisor)
    if value is None or step is None or not is_multiple_of(value, step):
        yield ValidationError(f'{instance!r} is not a multiple of {divisor}')


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


def escape_json_pointer_segment(segment: str) -> str:
    """One JSON Pointer segment, escaped per RFC 6901.

    `~` before `/`, or the second substitution rewrites the first. Written here
    because `referencing` only goes the other way, and inline: `Resource.pointer`
    unescapes as it walks and exposes nothing.
    """
    return segment.replace('~', '~0').replace('/', '~1')


def document_of(resolver: Resolver[Any]) -> str | None:
    """The document a resolver is based on. `lookup` re-bases onto its target."""
    return getattr(resolver, '_base_uri', None) or None


def ref_target(resolver: Resolver[Any], reference: str) -> tuple[str, str]:
    """Where `reference` lands from `resolver`'s document: the document, and the JSON Pointer.

    Split the way `Resolver.lookup` splits it. `urljoin` needs no special case
    for a bare `#...`: it appends the fragment to the base, which is what
    `lookup` shortcuts to.
    """
    document, pointer = urldefrag(urljoin(document_of(resolver) or '', reference))
    return document, pointer


def canonical(raml: Raml, document: str | None, pointer: str) -> str | None:
    """The canonical URI of the subschema at `pointer` in `document`, or `None`.

    Document plus JSON Pointer, which is the subschema's identity and the key
    its projection is shared under. `None` where the document is not the
    schema (`is_one_schema`): every schema written inline in an API or a
    library compiles under that one URI, so it identifies none of them.
    """
    return f'{document}#{pointer}' if is_one_schema(raml, document) else None


def is_one_schema(raml: Raml, document: str | None) -> bool:
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

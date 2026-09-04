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

import json
from typing import TYPE_CHECKING, Any, Final

from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.validators import Draft7Validator, validator_for
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT7, specification_with

from pyraml.errors import ErrorKind, RamlError
from pyraml.types.complex_ import ComplexKind
from pyraml.yamlnode import node_error

if TYPE_CHECKING:
    from referencing._core import Resolver

    from pyraml.positions import Position
    from pyraml.registry import Raml
    from pyraml.types.base import BaseShape
    from pyraml.yamlnode import Node

__all__ = [
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

    def compile(self, raw: str, location: str, position: Position | None) -> Any:
        """Compile one schema, resolving every reference it names.

        `location` may carry a JSON Pointer (`schema.json#/definitions/User`),
        which selects the subschema the RAML type stands for; the whole document
        is still the compilation unit, so a sibling definition it references
        resolves.
        """
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
        return validator_class(contents, registry=registry, _resolver=resolver)

    # -- reading --------------------------------------------------------------

    def _decode(self, raw: str, location: str, position: Position | None) -> Any:
        try:
            return json.loads(raw)
        except ValueError as err:
            raise RamlError.new(
                'invalid JSON in schema', location, position, kind=ErrorKind.PARSING, info={'error': str(err)}
            ) from err

    def _retrieve(self, uri: str) -> Resource[Any]:
        """`referencing`'s hook: every `$ref` target is read through the loader."""
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
            contents = json.loads(data)
        except ValueError as err:
            raise _LoadFailure(
                RamlError.new('invalid JSON in schema', uri, kind=ErrorKind.PARSING, info={'error': str(err)})
            ) from err
        resource = Resource.from_contents(contents, default_specification=DRAFT7)
        self._resources[uri] = resource
        return resource

    # -- reference resolution -------------------------------------------------

    def _lookup(self, resolver: Resolver[Any], ref: str, location: str, position: Position | None) -> Any:
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
    ) -> None:
        """Resolve every `$ref` now rather than at first validation.

        A schema library resolves lazily, so a reference to a missing file in a
        type nothing validates against would never be reported. The reference
        implementation compiles eagerly and the TCK expects that, so the walk is
        done here — best-effort by design: it skips the keywords whose values are
        data (`_DATA_KEYWORDS`) and the ones whose values are maps of subschemas
        (`_SCHEMA_MAPS`), which is enough to keep a user value that looks like a
        reference from being resolved as one.
        """
        if isinstance(node, list):
            for item in node:
                self._prefetch(item, resolver, specification, location, position, seen)
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
                self._prefetch(resolved.contents, resolved.resolver, specification, location, position, seen)

        for key, value in node.items():
            if key == '$ref' or key in _DATA_KEYWORDS:
                continue
            if key in _SCHEMA_MAPS and isinstance(value, dict):
                for member in value.values():
                    self._prefetch(member, resolver, specification, location, position, seen)
            else:
                self._prefetch(value, resolver, specification, location, position, seen)


def _specification_of(contents: Any) -> Any:
    """The draft a schema declares, or 7 — what the reference implementation assumes."""
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

    __slots__ = ('_cached_defs', '_cached_shape', 'raw', 'validator')

    def __init__(self, base: BaseShape, raw: str = '') -> None:
        super().__init__(base)
        #: The schema exactly as written.
        self.raw = raw
        self.validator: Any = None
        self._cached_shape: BaseShape | None = None
        self._cached_defs: dict[str, BaseShape] | None = None
        if raw:
            self.validator = schema_registry(base._raml).compile(raw, base.location, base.key_pos)  # noqa: SLF001

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

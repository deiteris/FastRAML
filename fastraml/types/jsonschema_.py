"""`JsonShape`: a RAML type declared by an external or inline JSON Schema.

docs/10-validation.md § 7. The type delegates both halves of validation to a
compiled JSON Schema: `check()` is done at construction, and `validate()` runs
the instance against the compiled validator. Its RAML reading (`as_shape`) and
its self-contained document (`as_schema`) are views built on demand.

The kind and its two helpers for consumers, `projected` and
`subschema_document`, are the public face of the JSON Schema modules:

- `schema_compile.py` compiles a schema and holds the parse's registry;
- `schema_view.py` holds the projection's walk context and view shapes;
- `schema_projection.py` projects a schema onto the nearest RAML shape;
- `schema_intersection.py` reduces an `allOf` for that projection;
- `schema_bundle.py` builds the self-contained document.

`JsonShape` lives apart from the other structured kinds because compiling and
projecting a schema need all of the above, which sit above `complex_.py` and
`scalars.py`; this module is imported by `shape.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastraml.errors import RamlError
from fastraml.types.complex_ import ArrayShape, ComplexKind, ObjectShape, RecursiveShape, UnionShape
from fastraml.types.schema_bundle import bundle
from fastraml.types.schema_compile import (
    CompiledSchema,
    SchemaRegistry,
    document_of,
    draft_of,
    escape_json_pointer_segment,
    is_one_schema,
    schema_registry,
)
from fastraml.types.schema_projection import project, project_reference
from fastraml.types.schema_view import Projection
from fastraml.types.values import index_path, key_path, rejected
from fastraml.yamlnode import node_error

if TYPE_CHECKING:
    from fastraml.types.base import BaseShape
    from fastraml.yamlnode import Node

__all__ = [
    'CompiledSchema',
    'JsonShape',
    'SchemaRegistry',
    'escape_json_pointer_segment',
    'projected',
    'schema_registry',
    'subschema_document',
]


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

    __slots__ = (
        '_cached_defs',
        '_cached_schema',
        '_cached_shape',
        '_compiled',
        '_projection_error',
        'raw',
        'validator',
    )

    def __init__(self, base: BaseShape, raw: str = '') -> None:
        super().__init__(base)
        #: The schema exactly as written.
        self.raw = raw
        self.validator: Any = None
        self._compiled: CompiledSchema | None = None
        self._cached_shape: BaseShape | None = None
        self._projection_error: RamlError | None = None
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
            raise SchemaRegistry.reference_error(
                err, str(getattr(err, 'ref', '')), self.base.location, self.base.value_pos
            ) from err

    # -- the projection (docs/10 § 7) -----------------------------------------

    def as_shape(self) -> BaseShape | None:
        """The nearest RAML shape to this schema, built once and cached.

        A **view** object, for consumers that want one model rather than two. It
        is not in `Raml.shapes`, it carries no positions, and it is marked
        unwrapped. Feeding one back into the parser's own passes is the failure
        mode to avoid: the model looks right until P9 tries to flatten it.

        `None` where the schema has no RAML reading (docs/10 § 7): the type
        stays an opaque JSON Schema, and `projection_error()` says why. The
        failure is cached as the projection is, and shared under the same
        canonical URI, so it is computed once.
        """
        if self._compiled is None:
            return None
        if self._cached_shape is None:
            registry = schema_registry(self.base._raml)  # noqa: SLF001 - one registry per parse
            uri = self.canonical_uri
            # The shared projection first: once any walk has one for this
            # subschema, a failure cached earlier no longer answers for it.
            draft = draft_of(self.validator)
            shared = registry.projected(uri, draft) if uri else None
            if shared is None and self._projection_error is not None:
                return None
            self._projection_error = None
            if shared is None:
                shared = self._project_alone(self._compiled, registry, uri, draft)
                if shared is None:
                    return None
            self._cached_shape, self._cached_defs = shared
        return self._cached_shape

    def _project_alone(
        self, compiled: CompiledSchema, registry: SchemaRegistry, uri: str | None, draft: str
    ) -> tuple[BaseShape, dict[str, BaseShape]] | None:
        """Walk the whole schema, or record why it has no projection."""
        failed = registry.failed(uri, draft) if uri else None
        if failed is None:
            defs: dict[str, BaseShape] = {}
            _, _, pointer = (uri or compiled.uri).partition('#')
            try:
                context = Projection(self.base, self.validator, compiled.resolver, defs, pointer)
                built = project(context, compiled.contents, {})
            except RamlError as err:
                failed = err
            else:
                if uri:
                    registry.share(uri, draft, built, defs)
                return built, defs
            if uri:
                registry.fail(uri, draft, failed)
        self._projection_error = failed
        return None

    def projection_error(self) -> RamlError | None:
        """Why `as_shape()` has no projection, or `None` if it has one.

        Projects on first call, as `as_shape` does. The error is the one
        the walk raised, kept rather than re-raised: a schema RAML cannot read
        is still a valid type, and a view reads it as an opaque schema.
        """
        self.as_shape()
        return self._projection_error

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
        Empty when the root has no projection: the definitions are read as the
        root's library, and there is no root to hold them.
        """
        root = self.as_shape()
        if self._compiled is None or root is None:
            return {}
        defs = dict(self._cached_defs or {})
        contents = self._compiled.contents
        declared: dict[str, BaseShape] = {}
        if isinstance(contents, dict):
            _, _, pointer = self._compiled.uri.partition('#')
            context = Projection(self.base, self.validator, self._compiled.resolver, defs, pointer)
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
                    declared[candidate] = project_reference(context, reference, {})
        # A local alias to `node.json` can register the same shape under both
        # `Node` (the author's definition) and `node` (the file stem). The
        # declared name wins; otherwise recursive references would name the
        # file stem without a corresponding Library declaration.
        result = dict(declared)
        used = set(result.values())
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
        if not is_one_schema(self.base._raml, self.document_uri):  # noqa: SLF001 - the parse's index
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
        return document_of(self._compiled.resolver)

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
            self._cached_schema = bundle(self._compiled, self.canonical_uri)
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
    A schema with no projection stays `base`, an opaque JSON Schema leaf whose
    `JsonShape` keeps the schema; it never raises (docs/10 § 7).

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

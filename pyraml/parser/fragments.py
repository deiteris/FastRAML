"""Fragment kinds, their decoders, and the cache that makes them sound.

Every non-API fragment may carry a root-level `uses:`, so every decoder does the
same two things first: strip `uses:`, then hand the remainder to the
kind-specific decoder. Two ordering rules in `parse_fragment` carry the weight
of this module (docs/04-fragments-and-namespaces.md section 6):

* the fragment is registered **before** its body is decoded, so `a.raml` →
  `b.raml` → `a.raml` terminates with a cyclic object graph instead of
  recursing forever;
* `uses:` is resolved **after** the body, as a separate stage. While a body is
  decoding every `LibraryLink.link` is `None`, and nothing dereferences it
  because all name resolution is deferred. Resolving first would be simpler and
  would break mutual imports.

A typed fragment's decoder pushes its **own** `ParseCtx`, never its caller's.
That is what makes the fragment cache sound: without it the same file would mean
different things at different inclusion sites.

Every declaration is now decoded here. The two `_raw_*` attributes that remain
are working buffers rather than seams: `_raw_endpoints` is handed to P4, which
runs after every fragment is decoded, and `_raw_secured_by` is harvested before
the main loop but decoded after it, because it names schemes the loop has yet to
declare. Each is released after its consumer succeeds.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from pyraml.domains import DomainLocation
from pyraml.errors import Accumulator, ErrorKind, RamlError
from pyraml.parser.annotations import DomainExtension, is_annotation_key, unmarshal_domain_extension
from pyraml.parser.directives import decode_secured_by, make_security_schemes
from pyraml.parser.documentation import DocumentationItem, decode_documentation_item
from pyraml.parser.endpoints import VALID_PROTOCOLS
from pyraml.parser.facets import make_scalar_facet, make_string_facet, scalar_str
from pyraml.parser.includes import note_include_ref, resolve_ref_uri, strip_uri_suffix
from pyraml.parser.references import resolve_library_reference, resolve_reference
from pyraml.parser.resourcetypes import ResourceTypeDefinition, make_resource_type_definition
from pyraml.parser.security import SecuritySchemeDefinition, make_security_scheme_definition
from pyraml.parser.traits import TraitDefinition, make_trait_definition
from pyraml.parser.uritemplates import extract_uri_template_params
from pyraml.registry import ParseCtx
from pyraml.types.examples import Example, make_example
from pyraml.types.shape import make_property_map, make_shape, unmarshal_types
from pyraml.uris import uri_base
from pyraml.yamlnode import (
    TAG_INCLUDE,
    TAG_MAP,
    TAG_NULL,
    TAG_STR,
    Node,
    NodeKind,
    compose,
    decode_source,
    node_error,
    pairs,
    read_head,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pyraml.positions import Position
    from pyraml.registry import Raml
    from pyraml.types.base import BaseShape, Property, ScalarFacet

__all__ = [
    'HEADS',
    'APIFragment',
    'DataTypeFragment',
    'DocumentationItemFragment',
    'Fragment',
    'FragmentKind',
    'Library',
    'LibraryLink',
    'NamedExample',
    'ReferenceResolver',
    'ResourceTypeFragment',
    'SecuritySchemeFragment',
    'SecuritySchemeResolver',
    'TraitFragment',
    'decode_resource_type_definitions',
    'decode_security_scheme_definitions',
    'decode_trait_definitions',
    'identify_fragment',
    'parse_fragment',
    'parse_library',
    'resolve_uses',
]


class FragmentKind(StrEnum):
    """The kinds of RAML document, named as the header spells them."""

    API = 'API'
    LIBRARY = 'Library'
    DATA_TYPE = 'DataType'
    NAMED_EXAMPLE = 'NamedExample'
    DOCUMENTATION_ITEM = 'DocumentationItem'
    RESOURCE_TYPE = 'ResourceType'
    TRAIT = 'Trait'
    ANNOTATION_TYPE = 'AnnotationTypeDeclaration'
    SECURITY_SCHEME = 'SecurityScheme'
    OVERLAY = 'Overlay'
    EXTENSION = 'Extension'


#: The first line of a document identifies it. Matching is exact, after the
#: line ending and trailing spaces are stripped. See docs/03 section 3.
HEADS: Final[Mapping[str, FragmentKind]] = {
    '#%RAML 1.0': FragmentKind.API,
    '#%RAML 1.0 Library': FragmentKind.LIBRARY,
    '#%RAML 1.0 DataType': FragmentKind.DATA_TYPE,
    '#%RAML 1.0 NamedExample': FragmentKind.NAMED_EXAMPLE,
    '#%RAML 1.0 DocumentationItem': FragmentKind.DOCUMENTATION_ITEM,
    '#%RAML 1.0 ResourceType': FragmentKind.RESOURCE_TYPE,
    '#%RAML 1.0 Trait': FragmentKind.TRAIT,
    '#%RAML 1.0 AnnotationTypeDeclaration': FragmentKind.ANNOTATION_TYPE,
    '#%RAML 1.0 SecurityScheme': FragmentKind.SECURITY_SCHEME,
    '#%RAML 1.0 Overlay': FragmentKind.OVERLAY,
    '#%RAML 1.0 Extension': FragmentKind.EXTENSION,
}


def identify_fragment(head: str) -> FragmentKind | None:
    """The kind a document's first line declares, or `None` if it declares none."""
    return HEADS.get(head)


#: What an annotation written at a fragment's root is being applied to. The two
#: names that do not simply match are `DataType`, whose root *is* a type
#: declaration, and `AnnotationTypeDeclaration`, whose root is an annotation
#: type. Narrower sites push over this via `Raml.target_scope`.
#: See docs/09-security-and-annotations.md section B5.
FRAGMENT_TARGETS: Final[Mapping[FragmentKind, DomainLocation]] = {
    FragmentKind.API: DomainLocation.API,
    FragmentKind.LIBRARY: DomainLocation.LIBRARY,
    FragmentKind.DATA_TYPE: DomainLocation.TYPE_DECLARATION,
    FragmentKind.NAMED_EXAMPLE: DomainLocation.EXAMPLE,
    FragmentKind.DOCUMENTATION_ITEM: DomainLocation.DOCUMENTATION_ITEM,
    FragmentKind.RESOURCE_TYPE: DomainLocation.RESOURCE_TYPE,
    FragmentKind.TRAIT: DomainLocation.TRAIT,
    FragmentKind.ANNOTATION_TYPE: DomainLocation.ANNOTATION_TYPE,
    FragmentKind.SECURITY_SCHEME: DomainLocation.SECURITY_SCHEME,
    FragmentKind.OVERLAY: DomainLocation.OVERLAY,
    FragmentKind.EXTENSION: DomainLocation.EXTENSION,
}


# -- facet names --------------------------------------------------------------

FACET_USES: Final = 'uses'
FACET_TYPES: Final = 'types'
FACET_SCHEMAS: Final = 'schemas'
FACET_ANNOTATION_TYPES: Final = 'annotationTypes'
FACET_RESOURCE_TYPES: Final = 'resourceTypes'
FACET_TRAITS: Final = 'traits'
FACET_SECURITY_SCHEMES: Final = 'securitySchemes'
FACET_SECURED_BY: Final = 'securedBy'
FACET_USAGE: Final = 'usage'
FACET_TITLE: Final = 'title'
FACET_DESCRIPTION: Final = 'description'
FACET_VERSION: Final = 'version'
FACET_BASE_URI: Final = 'baseUri'
FACET_BASE_URI_PARAMETERS: Final = 'baseUriParameters'
FACET_MEDIA_TYPE: Final = 'mediaType'
FACET_PROTOCOLS: Final = 'protocols'
FACET_DOCUMENTATION: Final = 'documentation'


@runtime_checkable
class Fragment(Protocol):
    """Anything a RAML file can decode to. Capability is checked, not inherited."""

    location: str


@runtime_checkable
class ReferenceResolver(Fragment, Protocol):
    """A fragment that can resolve a name written inside it.

    Implemented by *all* typed fragments, because all of them may carry `uses:`
    and therefore all of them may resolve a qualified name.
    """

    def reference_type(self, name: str) -> BaseShape: ...

    def reference_annotation_type(self, name: str) -> BaseShape: ...

    def resource_type_definition(self, name: str) -> ResourceTypeDefinition: ...

    def trait_definition(self, name: str) -> TraitDefinition: ...

    def library_link(self, prefix: str) -> LibraryLink | None:
        """The `uses:` entry a qualified name's prefix names, if any.

        On the protocol rather than reached through a fragment's `uses` field
        so that P7 can emit the library half of a `lib.Type` reference
        (docs/06 section 3.2) through the anchor it already holds, without
        `types/` importing this module at runtime.
        """
        ...


@runtime_checkable
class SecuritySchemeResolver(Protocol):
    """Only `Library` and `APIFragment`: only those declare security schemes."""

    def security_scheme_definition(self, name: str) -> SecuritySchemeDefinition: ...


# -- uses: --------------------------------------------------------------------


class LibraryLink:
    """One entry of a `uses:` map. `link` stays `None` until the P3 stage."""

    __slots__ = ('id', 'key_pos', 'link', 'location', 'value', 'value_pos')

    def __init__(self, id: int, value: str, location: str, key_pos: Position, value_pos: Position) -> None:  # noqa: A002 - `id` is the model's field name across every entity
        self.id = id
        self.value = value
        self.location = location
        self.key_pos = key_pos
        self.value_pos = value_pos
        self.link: Library | None = None

    def __repr__(self) -> str:
        return f'LibraryLink({self.value!r}, linked={self.link is not None})'


def unmarshal_uses(raml: Raml, value_node: Node, location: str) -> dict[str, LibraryLink]:
    if value_node.tag == TAG_NULL:
        return {}
    if value_node.kind is not NodeKind.MAPPING:
        raise node_error('uses must be a map', location, value_node)

    uses: dict[str, LibraryLink] = {}
    for key, value in pairs(value_node):
        if key.value in uses:
            raise node_error('duplicate library name', location, key, info={'library': key.value})
        uses[key.value] = LibraryLink(
            id=raml.next_id(),
            value=value.value,
            location=location,
            key_pos=key.position,
            value_pos=value.full_position,
        )
    return uses


def filter_fragment_uses(raml: Raml, node: Node, location: str) -> tuple[Node, dict[str, LibraryLink]]:
    """Strip a root-level `uses:` and return the remainder plus the links.

    The remainder is a shallow copy: the original node is left untouched, and
    every retained child keeps its identity so provenance lookups still work.
    """
    if node.kind is not NodeKind.MAPPING:
        raise node_error('must be map', location, node)

    uses: dict[str, LibraryLink] = {}
    kept: list[Node] = []
    for key, value in pairs(node):
        if key.value == FACET_USES:
            uses = unmarshal_uses(raml, value, location)
        else:
            kept.append(key)
            kept.append(value)

    filtered = Node(node.kind, node.tag, node.value, kept, node.line, node.column, node.end_line, node.end_column)
    return filtered, uses


def resolve_uses(raml: Raml, uses: Mapping[str, LibraryLink], location: str) -> None:
    """Parse every library a fragment imports and link it.

    Recursive: each library's own `uses:` is resolved by its own decode. A
    failure is recorded and the remaining imports are still resolved, so one
    missing library does not hide the others.
    """
    accumulator = Accumulator()
    for link in uses.values():
        try:
            link.link = parse_library(raml, resolve_ref_uri(raml, link.value, location))
        except RamlError as err:
            accumulator.add(RamlError.wrap('parse uses library', err, location, link.key_pos))
        except (OSError, ValueError) as err:
            accumulator.add(RamlError.wrap('resolve uses URI', err, location, link.key_pos))
    accumulator.raise_if_any()


# -- fragment classes ---------------------------------------------------------


class _BaseFragment:
    """State every fragment has: an id, its location, and its `uses:` map."""

    __slots__ = ('_raml', 'id', 'location', 'uses')

    def __init__(self, raml: Raml, location: str) -> None:
        self.id = raml.next_id()
        self.location = location
        self.uses: dict[str, LibraryLink] = {}
        self._raml = raml

    def __repr__(self) -> str:
        return f'{type(self).__name__}({self.location!r})'

    def decode(self, node: Node) -> None:  # pragma: no cover - overridden everywhere
        raise NotImplementedError

    def library_link(self, prefix: str) -> LibraryLink | None:
        """One `uses:` lookup, shared by every fragment kind.

        Unlike the four name resolvers, this one has no local-declaration half
        and no annotation fallback, so `_UsesOnlyFragment` does not override it.
        """
        return self.uses.get(prefix)


class _UsesOnlyFragment(_BaseFragment):
    """A typed fragment with no declarations of its own.

    Its four resolvers are identical — every name must be qualified and must
    come through `uses:` — so they are written once here rather than six times.
    This is not a capability base class: what a fragment *can* do is still
    discovered by protocol check, and `Library`/`APIFragment` override all four.
    """

    __slots__ = ()

    def reference_type(self, name: str) -> BaseShape:
        return resolve_library_reference(self.uses, name, _pick_type)

    def reference_annotation_type(self, name: str) -> BaseShape:
        # An annotation type declaration has the same syntax as a data type and
        # may extend one, so the lookup falls back to `types`. docs/04 § 3.1.
        try:
            return resolve_library_reference(self.uses, name, _pick_annotation_type)
        except LookupError:
            return resolve_library_reference(self.uses, name, _pick_type)

    def resource_type_definition(self, name: str) -> ResourceTypeDefinition:
        return resolve_library_reference(self.uses, name, _pick_resource_type)

    def trait_definition(self, name: str) -> TraitDefinition:
        return resolve_library_reference(self.uses, name, _pick_trait)


def _pick_type(library: Library, name: str) -> BaseShape | None:
    return library.types.get(name)


def _pick_annotation_type(library: Library, name: str) -> BaseShape | None:
    return library.annotation_types.get(name)


def _pick_trait(library: Library, name: str) -> TraitDefinition | None:
    return library.traits.get(name)


def _pick_resource_type(library: Library, name: str) -> ResourceTypeDefinition | None:
    return library.resource_types.get(name)


def _pick_security_scheme(library: Library, name: str) -> SecuritySchemeDefinition | None:
    return library.security_schemes.get(name)


class Library(_BaseFragment):
    """`#%RAML 1.0 Library` — the only fragment that declares all five kinds."""

    __slots__ = (
        'annotation_types',
        'annotations',
        'resource_types',
        'security_schemes',
        'traits',
        'types',
        'usage',
    )

    def __init__(self, raml: Raml, location: str) -> None:
        super().__init__(raml, location)
        self.usage: ScalarFacet[str] | None = None
        self.types: dict[str, BaseShape] = {}
        self.annotation_types: dict[str, BaseShape] = {}
        self.traits: dict[str, TraitDefinition] = {}
        self.resource_types: dict[str, ResourceTypeDefinition] = {}
        self.security_schemes: dict[str, SecuritySchemeDefinition] = {}
        self.annotations: dict[str, DomainExtension] = {}

    # -- ReferenceResolver / SecuritySchemeResolver ---------------------------

    def reference_type(self, name: str) -> BaseShape:
        return resolve_reference(self.types, self.uses, name, _pick_type)

    def reference_annotation_type(self, name: str) -> BaseShape:
        try:
            return resolve_reference(self.annotation_types, self.uses, name, _pick_annotation_type)
        except LookupError:
            return resolve_reference(self.types, self.uses, name, _pick_type)

    def resource_type_definition(self, name: str) -> ResourceTypeDefinition:
        return resolve_reference(self.resource_types, self.uses, name, _pick_resource_type)

    def trait_definition(self, name: str) -> TraitDefinition:
        return resolve_reference(self.traits, self.uses, name, _pick_trait)

    def security_scheme_definition(self, name: str) -> SecuritySchemeDefinition:
        return resolve_reference(self.security_schemes, self.uses, name, _pick_security_scheme)

    # -- decoding -------------------------------------------------------------

    def decode(self, node: Node) -> None:
        if node.kind is not NodeKind.MAPPING:
            raise node_error('must be map', self.location, node)

        raml = self._raml
        accumulator = Accumulator()
        declarations = _Declarations(self.location)
        for key, value in pairs(node):
            name = key.value
            try:
                if name == FACET_USES:
                    self.uses = unmarshal_uses(raml, value, self.location)
                elif name == FACET_USAGE:
                    self.usage = make_string_facet(raml, key, value, self.location)
                elif name in (FACET_TYPES, FACET_SCHEMAS):
                    self.types = unmarshal_types(raml, declarations.types(key, value), self.location)
                elif name == FACET_ANNOTATION_TYPES:
                    self.annotation_types = unmarshal_types(raml, value, self.location, is_annotation=True)
                elif name == FACET_TRAITS:
                    self.traits = decode_trait_definitions(raml, value, self.location)
                elif name == FACET_RESOURCE_TYPES:
                    self.resource_types = decode_resource_type_definitions(raml, value, self.location)
                elif name == FACET_SECURITY_SCHEMES:
                    self.security_schemes = decode_security_scheme_definitions(raml, value, self.location)
                elif is_annotation_key(name):
                    extension = unmarshal_domain_extension(raml, self.location, key, value)
                    self.annotations[extension.name] = extension
                else:
                    raise node_error('unknown field', self.location, key, info={'field': name})
            except RamlError as err:
                accumulator.add(err)
        accumulator.raise_if_any()


class APIFragment(_BaseFragment):
    """`#%RAML 1.0` — the root document."""

    __slots__ = (
        '_raw_endpoints',
        '_raw_secured_by',
        'annotation_types',
        'annotations',
        'base_uri',
        'base_uri_parameters',
        'description',
        'documentation',
        'media_types',
        'protocols',
        'resource_types',
        'security_schemes',
        'title',
        'traits',
        'types',
        'version',
    )

    def __init__(self, raml: Raml, location: str) -> None:
        super().__init__(raml, location)
        self.title: ScalarFacet[str] | None = None
        self.description: ScalarFacet[str] | None = None
        self.version: ScalarFacet[str] | None = None
        self.base_uri: ScalarFacet[str] | None = None
        self.protocols: list[ScalarFacet[str]] = []
        self.media_types: list[ScalarFacet[str]] = []
        self.documentation: list[DocumentationItem] = []
        self.types: dict[str, BaseShape] = {}
        self.annotation_types: dict[str, BaseShape] = {}
        self.traits: dict[str, TraitDefinition] = {}
        self.resource_types: dict[str, ResourceTypeDefinition] = {}
        self.security_schemes: dict[str, SecuritySchemeDefinition] = {}
        self.annotations: dict[str, DomainExtension] = {}
        self.base_uri_parameters: dict[str, Property] = {}
        # A seam: `securedBy:` is harvested before the main loop but decoded
        # after it, because it names schemes the loop has yet to declare.
        self._raw_secured_by: Node | None = None
        #: `(key, value)` pairs for every `/relativeUri` key, in document order.
        #: Phase 5 turns them into the stage-1 endpoint IR.
        self._raw_endpoints: list[tuple[Node, Node]] = []

    # -- ReferenceResolver / SecuritySchemeResolver ---------------------------

    def reference_type(self, name: str) -> BaseShape:
        return resolve_reference(self.types, self.uses, name, _pick_type)

    def reference_annotation_type(self, name: str) -> BaseShape:
        try:
            return resolve_reference(self.annotation_types, self.uses, name, _pick_annotation_type)
        except LookupError:
            return resolve_reference(self.types, self.uses, name, _pick_type)

    def resource_type_definition(self, name: str) -> ResourceTypeDefinition:
        return resolve_reference(self.resource_types, self.uses, name, _pick_resource_type)

    def trait_definition(self, name: str) -> TraitDefinition:
        return resolve_reference(self.traits, self.uses, name, _pick_trait)

    def security_scheme_definition(self, name: str) -> SecuritySchemeDefinition:
        return resolve_reference(self.security_schemes, self.uses, name, _pick_security_scheme)

    # -- decoding -------------------------------------------------------------

    def decode(self, node: Node) -> None:
        if node.kind is not NodeKind.MAPPING:
            raise node_error('must be map', self.location, node)

        accumulator = Accumulator()
        remainder = self._preprocess(node, accumulator)

        declarations = _Declarations(self.location)
        for key, value in remainder:
            try:
                self._decode_key(key, value, declarations)
            except RamlError as err:
                accumulator.add(err)

        if self._raw_secured_by is not None:
            try:
                refs = decode_secured_by(self._raw_secured_by, self.location, ParseCtx(anchor=self))
                self._raml.global_secured_by = make_security_schemes(self._raml, refs)
            except RamlError as err:
                accumulator.add(err)

        if self.title is None:
            accumulator.add(node_error('title is required', self.location, node))
        accumulator.raise_if_any()
        self._raw_secured_by = None

    def _decode_key(self, key: Node, value: Node, declarations: _Declarations) -> None:
        if self._decode_root_facet(key, value) or self._retain_declarations(key, value, declarations):
            return
        name = key.value
        if is_annotation_key(name):
            extension = unmarshal_domain_extension(self._raml, self.location, key, value)
            self.annotations[extension.name] = extension
        elif name.startswith('/'):
            # Endpoints are not decoded here: they become stage-1 IR in Phase 5,
            # because the trait and resource-type merge runs on the YAML tree.
            self._raw_endpoints.append((key, value))
        else:
            raise node_error('unknown field', self.location, key, info={'field': name})

    def _decode_root_facet(self, key: Node, value: Node) -> bool:
        """The keys Phase 1 owns. Returns whether the key was one of them."""
        raml = self._raml
        name = key.value
        if name == FACET_TITLE:
            title = make_string_facet(raml, key, value, self.location)
            if not title.value:
                raise node_error('title must not be empty', self.location, key)
            self.title = title
        elif name == FACET_DESCRIPTION:
            self.description = make_string_facet(raml, key, value, self.location)
        elif name == FACET_VERSION:
            self.version = make_string_facet(raml, key, value, self.location)
        elif name == FACET_BASE_URI:
            facet = make_string_facet(raml, key, value, self.location)
            # A base URI is a URI template like a resource's own, so it gets the
            # same parse: `http://{myapi.com` is an unclosed expression, not a
            # hostname (docs/08 section 8.2).
            extract_uri_template_params(facet.value, self.location, facet.value_pos)
            self.base_uri = facet
        elif name == FACET_DOCUMENTATION:
            self.documentation = unmarshal_documentation_items(raml, key, value, self.location)
        elif name == FACET_USES:
            self.uses = unmarshal_uses(raml, value, self.location)
        else:
            return False
        return True

    def _retain_declarations(self, key: Node, value: Node, declarations: _Declarations) -> bool:
        """The seams: kept as written for Phase 2 (types), 6 (templates), 7 (security)."""
        name = key.value
        if name in (FACET_TYPES, FACET_SCHEMAS):
            self.types = unmarshal_types(self._raml, declarations.types(key, value), self.location)
        elif name == FACET_ANNOTATION_TYPES:
            self.annotation_types = unmarshal_types(self._raml, value, self.location, is_annotation=True)
        elif name == FACET_BASE_URI_PARAMETERS:
            self.base_uri_parameters = make_property_map(self._raml, value, self.location)
        elif name == FACET_TRAITS:
            self.traits = decode_trait_definitions(self._raml, value, self.location)
        elif name == FACET_RESOURCE_TYPES:
            self.resource_types = decode_resource_type_definitions(self._raml, value, self.location)
        elif name == FACET_SECURITY_SCHEMES:
            self.security_schemes = decode_security_scheme_definitions(self._raml, value, self.location)
        else:
            return False
        return True

    def _preprocess(self, node: Node, accumulator: Accumulator) -> list[tuple[Node, Node]]:
        """Extract the three global settings, and return the remaining pairs.

        `mediaType`, `protocols` and `securedBy` are needed by everything
        decoded afterwards — body media types, operation protocols, and the
        default security of every operation — so they are harvested before the
        main loop rather than in document order. See docs/04 section 5.1.
        """
        raml = self._raml
        remainder: list[tuple[Node, Node]] = []
        for key, value in pairs(node):
            try:
                if key.value == FACET_PROTOCOLS:
                    self.protocols = self._unmarshal_protocols(value)
                    raml.global_protocols = [item.value for item in self.protocols]
                elif key.value == FACET_MEDIA_TYPE:
                    self.media_types = self._unmarshal_media_types(key, value)
                    raml.global_media_types = [item.value for item in self.media_types]
                elif key.value == FACET_SECURED_BY:
                    # Kept, not decoded: the names it uses are declared by a
                    # `securitySchemes:` key the main loop has not reached yet.
                    self._raw_secured_by = value
                else:
                    remainder.append((key, value))
            except RamlError as err:
                accumulator.add(err)
        return remainder

    def _unmarshal_protocols(self, node: Node) -> list[ScalarFacet[str]]:
        if node.kind is not NodeKind.SEQUENCE:
            raise node_error('protocols must be an array', self.location, node)
        if not node.content:
            raise node_error('protocols must not be empty', self.location, node)
        protocols = []
        for item in node.content:
            facet = make_scalar_facet(self._raml, None, item, self.location, scalar_str)
            if facet.value.lower() not in VALID_PROTOCOLS:
                raise node_error('unknown protocol', self.location, item, info={'protocol': facet.value})
            protocols.append(facet)
        return protocols

    def _unmarshal_media_types(self, key: Node, node: Node) -> list[ScalarFacet[str]]:
        if node.kind is NodeKind.SCALAR:
            items = [make_scalar_facet(self._raml, key, node, self.location, scalar_str)]
        elif node.kind is NodeKind.SEQUENCE:
            items = [make_scalar_facet(self._raml, None, item, self.location, scalar_str) for item in node.content]
        else:
            raise node_error('media type must be a string or sequence', self.location, node)

        if not items:
            raise node_error('media type must not be empty', self.location, node)
        for item in items:
            if not _is_valid_media_type(item.value):
                raise node_error('invalid media type', self.location, node, info={'media type': item.value})
        return items


class DataTypeFragment(_UsesOnlyFragment):
    """`#%RAML 1.0 DataType` — the whole document is one type declaration."""

    __slots__ = ('shape',)

    def __init__(self, raml: Raml, location: str) -> None:
        super().__init__(raml, location)
        self.shape: BaseShape | None = None

    def decode(self, node: Node) -> None:
        filtered, uses = filter_fragment_uses(self._raml, node, self.location)
        self.uses = uses
        self._build(filtered)

    def decode_json_schema(self, text: str) -> None:
        """Wrap raw JSON Schema text as `{type: "<raw json>"}`.

        That synthetic mapping is what the ordinary shape builder already
        understands, so an external schema needs no branch of its own
        downstream. See docs/04 section 5.2.
        """
        self._build(
            Node(
                NodeKind.MAPPING,
                TAG_MAP,
                '',
                [Node(NodeKind.SCALAR, TAG_STR, 'type'), Node(NodeKind.SCALAR, TAG_STR, text)],
            )
        )

    def _build(self, declaration: Node) -> None:
        """The whole remaining mapping is the declaration (docs/04 section 5.2).

        A synthetic key node carrying the file's base name gives the shape a
        sensible name; from there it is an ordinary declaration.
        """
        key = Node(NodeKind.SCALAR, TAG_STR, self.declared_name)
        self.shape = make_shape(self._raml, key, declaration, self.location)
        self._raml.put_typedef(self.location, self.shape)

    @property
    def declared_name(self) -> str:
        """The name Phase 2 gives the shape: the file's base name."""
        return uri_base(self.location)


class NamedExample(_UsesOnlyFragment):
    """`#%RAML 1.0 NamedExample` — a mapping of example name to example."""

    __slots__ = ('examples',)

    def __init__(self, raml: Raml, location: str) -> None:
        super().__init__(raml, location)
        self.examples: dict[str, Example] = {}

    def decode(self, node: Node) -> None:
        filtered, uses = filter_fragment_uses(self._raml, node, self.location)
        self.uses = uses
        for key, value in pairs(filtered):
            self.examples[key.value] = make_example(self._raml, value, key.value, self.location)


class DocumentationItemFragment(_UsesOnlyFragment):
    """`#%RAML 1.0 DocumentationItem` — one `{title, content}` pair."""

    __slots__ = ('item',)

    def __init__(self, raml: Raml, location: str) -> None:
        super().__init__(raml, location)
        self.item: DocumentationItem | None = None

    def decode(self, node: Node) -> None:
        filtered, uses = filter_fragment_uses(self._raml, node, self.location)
        self.uses = uses
        item = decode_documentation_item(self._raml, filtered, self.location)
        item.link = self
        self.item = item


class _DefinitionFragment(_UsesOnlyFragment):
    """A fragment whose body is one template or scheme definition.

    Trait, ResourceType and SecurityScheme differ only in which builder the body
    goes to, so stripping `uses:` is shared. The first two override `decode` to
    build their definition; SecurityScheme keeps the seam until Phase 7.
    """

    __slots__ = ('_raw_definition', 'definition')

    def __init__(self, raml: Raml, location: str) -> None:
        super().__init__(raml, location)
        self.definition: Any = None
        self._raw_definition: Node | None = None

    def decode(self, node: Node) -> None:
        filtered, uses = filter_fragment_uses(self._raml, node, self.location)
        self.uses = uses
        self._raw_definition = filtered


class TraitFragment(_DefinitionFragment):
    """`#%RAML 1.0 Trait` — the whole document is one trait definition."""

    __slots__ = ()

    def decode(self, node: Node) -> None:
        super().decode(node)
        self.definition = _one_definition(
            self._raml, None, self._raw_definition, self.location, make_trait_definition, FragmentKind.TRAIT
        )
        self.definition.name = uri_base(self.location)


class ResourceTypeFragment(_DefinitionFragment):
    """`#%RAML 1.0 ResourceType` — the whole document is one definition."""

    __slots__ = ()

    def decode(self, node: Node) -> None:
        super().decode(node)
        self.definition = _one_definition(
            self._raml,
            None,
            self._raw_definition,
            self.location,
            make_resource_type_definition,
            FragmentKind.RESOURCE_TYPE,
        )
        self.definition.name = uri_base(self.location)


class SecuritySchemeFragment(_DefinitionFragment):
    """`#%RAML 1.0 SecurityScheme` — the whole document is one scheme.

    Its shapes are deliberately **not** resolved when the fragment is decoded.
    A `describedBy` body gets embedded into an operation through `securedBy`, so
    those shapes belong to the same global resolution batch as everything else;
    resolving them here would race the including document's `uses:` resolution,
    which has not run yet. See docs/04 section 5.4.
    """

    __slots__ = ()

    def decode(self, node: Node) -> None:
        super().decode(node)
        self.definition = _one_definition(
            self._raml,
            None,
            self._raw_definition,
            self.location,
            make_security_scheme_definition,
            FragmentKind.SECURITY_SCHEME,
        )
        self.definition.name = uri_base(self.location)


# -- traits: and resourceTypes: -----------------------------------------------
#
# The definitions live in `traits.py` and `resourcetypes.py`; only the two
# functions below are here, because following a definition's `!include` means
# parsing a fragment, which this module owns. docs/02 section 3 forbids the
# reverse import and rules out a deferred one.


def _one_definition(  # noqa: PLR0913, PLR0917 - `make` and `kind` are what let one function serve two
    raml: Raml,
    key: Node | None,
    value: Node | None,
    location: str,
    make: Callable[[Raml, Node | None, Node, str], Any],
    kind: FragmentKind,
) -> Any:
    """Build one definition, following an `!include` to the linked fragment's."""
    if value is None:
        value = Node(NodeKind.SCALAR, TAG_NULL)
    definition = make(raml, key, value, location)
    if definition.link_uri:
        fragment = parse_fragment(raml, definition.link_uri, kind)
        definition.link = getattr(fragment, 'definition', None)
    return definition


def _definitions(
    raml: Raml,
    node: Node,
    location: str,
    make: Callable[[Raml, Node | None, Node, str], Any],
    kind: FragmentKind,
) -> dict[str, Any]:
    """Decode a `traits:` or `resourceTypes:` map, one definition per name."""
    if node.tag == TAG_NULL:
        return {}
    if node.kind is not NodeKind.MAPPING:
        raise node_error(f'{kind} declarations must be a mapping', location, node)
    declared: dict[str, Any] = {}
    accumulator = Accumulator()
    for key, value in pairs(node):
        try:
            declared[key.value] = _one_definition(raml, key, value, location, make, kind)
        except RamlError as err:
            accumulator.add(err)
    accumulator.raise_if_any()
    return declared


def decode_trait_definitions(raml: Raml, node: Node, location: str) -> dict[str, TraitDefinition]:
    return _definitions(raml, node, location, make_trait_definition, FragmentKind.TRAIT)


def decode_resource_type_definitions(raml: Raml, node: Node, location: str) -> dict[str, ResourceTypeDefinition]:
    return _definitions(raml, node, location, make_resource_type_definition, FragmentKind.RESOURCE_TYPE)


def decode_security_scheme_definitions(raml: Raml, node: Node, location: str) -> dict[str, SecuritySchemeDefinition]:
    return _definitions(raml, node, location, make_security_scheme_definition, FragmentKind.SECURITY_SCHEME)


class _Declarations:
    """Tracks the `types:` / `schemas:` mutual exclusion within one document."""

    __slots__ = ('_location', '_seen')

    def __init__(self, location: str) -> None:
        self._location = location
        self._seen = ''

    def types(self, key: Node, value: Node) -> Node:
        if self._seen and self._seen != key.value:
            raise node_error(
                'types and schemas are mutually exclusive', self._location, value, info={'field': key.value}
            )
        self._seen = key.value
        return value


def unmarshal_documentation_items(
    raml: Raml, key_node: Node, value_node: Node, location: str
) -> list[DocumentationItem]:
    """Decode `documentation:`, which is a sequence of items or includes."""
    if value_node.kind is not NodeKind.SEQUENCE:
        raise node_error('documentation must be a sequence', location, key_node)

    items: list[DocumentationItem] = []
    for item_node in value_node.content:
        if item_node.tag == TAG_INCLUDE:
            # The target is a fragment with its own cache, so the reference is
            # noted rather than resolved: resolving would read the file twice.
            target = note_include_ref(raml, item_node, location)
            fragment = parse_fragment(raml, target, FragmentKind.DOCUMENTATION_ITEM)
            if isinstance(fragment, DocumentationItemFragment) and fragment.item is not None:
                items.append(fragment.item)
        else:
            items.append(decode_documentation_item(raml, item_node, location))
    return items


# -- media types --------------------------------------------------------------


def _is_valid_media_type(value: str) -> bool:
    """`type/subtype`, where both parts are non-empty RFC 2045 tokens."""
    kind, separator, subtype = value.partition('/')
    if not separator or not kind or not subtype:
        return False
    return _is_media_token(kind) and _is_media_token(subtype)


def _is_media_token(value: str) -> bool:
    return all(character.isalnum() or character in '-.+' for character in value)


# -- the fragment cache -------------------------------------------------------

_FRAGMENT_CLASSES: Final[Mapping[FragmentKind, Callable[[Raml, str], _BaseFragment]]] = {
    FragmentKind.API: APIFragment,
    FragmentKind.LIBRARY: Library,
    FragmentKind.DATA_TYPE: DataTypeFragment,
    FragmentKind.ANNOTATION_TYPE: DataTypeFragment,
    FragmentKind.NAMED_EXAMPLE: NamedExample,
    FragmentKind.DOCUMENTATION_ITEM: DocumentationItemFragment,
    FragmentKind.TRAIT: TraitFragment,
    FragmentKind.RESOURCE_TYPE: ResourceTypeFragment,
    FragmentKind.SECURITY_SCHEME: SecuritySchemeFragment,
}


def make_fragment(raml: Raml, kind: FragmentKind, uri: str) -> _BaseFragment:
    """The empty fragment object for a kind. Overlay and Extension are v1.1."""
    factory = _FRAGMENT_CLASSES.get(kind)
    if factory is None:
        raise RamlError.new('fragment kind not supported', uri, info={'kind': str(kind)}, kind=ErrorKind.PARSING)
    return factory(raml, uri)


def check_fragment_kind(text: str, uri: str, kind: FragmentKind) -> None:
    """Verify the document's header against the kind its context demands.

    Fails fast rather than accumulating: a fragment of the wrong kind decodes to
    something that misrepresents its source. A `.json` file where a `DataType`
    is expected skips the check — it is an external JSON Schema and has no RAML
    header — and an `AnnotationTypeDeclaration` is accepted where a `DataType`
    is expected, the two being structurally identical.
    """
    # The extension is taken past a `#pointer`: `order.json#/definitions/Item`
    # is a JSON include, not an include of something ending `.json#`.
    path = strip_uri_suffix(uri).lower()
    if kind is FragmentKind.DATA_TYPE and path.endswith('.json'):
        return
    if path.endswith('.xsd'):
        # Deviation D1. Reported here rather than left to the header check,
        # which would tell the author their XSD has an unrecognised RAML
        # header — true, unhelpful, and the wrong thing to go and fix. Only
        # `.xsd`: an `!include` of `.xml` is a scalar include and may well be a
        # legitimate example.
        raise RamlError.new(
            'xml schema external types are not supported', uri, info={'path': uri}, kind=ErrorKind.PARSING
        )

    head = read_head(text)
    found = identify_fragment(head)
    if found is None:
        raise RamlError.new('unknown fragment kind', uri, info={'head': head}, kind=ErrorKind.PARSING)
    if found is kind:
        return
    if kind is FragmentKind.DATA_TYPE and found is FragmentKind.ANNOTATION_TYPE:
        return
    raise RamlError.new(
        'unexpected fragment kind', uri, info={'expected': str(kind), 'found': str(found)}, kind=ErrorKind.PARSING
    )


def load_fragment_text(raml: Raml, uri: str) -> str:
    try:
        data = raml.loader.load(uri)
    except OSError as err:
        raise RamlError.wrap('load resource', err, uri, kind=ErrorKind.LOADING) from err
    return decode_source(data)


def parse_fragment(raml: Raml, uri: str, kind: FragmentKind) -> Fragment:
    """Return the fragment at `uri`, decoding it at most once per parse."""
    cached = raml.get_fragment(uri)
    if cached is not None:
        return cached
    text = load_fragment_text(raml, uri)
    check_fragment_kind(text, uri, kind)
    return decode_fragment(raml, uri, kind, text)


def decode_fragment(raml: Raml, uri: str, kind: FragmentKind, text: str) -> Fragment:
    """Register, decode, then resolve `uses:` — in that order. See the module docstring."""
    # The extension is taken past a `#pointer`, as in `check_fragment_kind`:
    # `schema.json#/definitions/User` is a JSON include of an inner element, not
    # a RAML DataType. Testing the raw URI misses the pointer form, and the file
    # then decodes as RAML with `$schema` and `definitions` as custom facets.
    if kind is FragmentKind.DATA_TYPE and strip_uri_suffix(uri).lower().endswith('.json'):
        return _decode_json_data_type(raml, uri, text)

    fragment = make_fragment(raml, kind, uri)
    # Registered before the body is decoded: a cycle back to this file resolves
    # to the in-progress object instead of recursing.
    raml.put_fragment(uri, fragment)
    anchor = fragment if isinstance(fragment, ReferenceResolver) else None
    if anchor is not None:
        # Indexed here, where the capability check already happens, so that P7's
        # fallback for a shape built outside any parse context is a dict lookup
        # rather than a second isinstance in `types/` (docs/04 section 4.2).
        raml.put_resolver(uri, anchor)
    raml.push_ctx(ParseCtx(anchor=anchor, target=FRAGMENT_TARGETS[kind]))
    try:
        root = compose(text, uri=uri, max_depth=raml.max_depth)
        raml.store_source_node(uri, root)
        fragment.decode(root)
    finally:
        raml.pop_ctx()

    # After the body, never before: mutual imports depend on this ordering.
    resolve_uses(raml, fragment.uses, uri)
    return fragment


def _decode_json_data_type(raml: Raml, uri: str, text: str) -> DataTypeFragment:
    """An external JSON Schema: no RAML header, no `uses:`, no YAML compose."""
    fragment = DataTypeFragment(raml, uri)
    raml.put_fragment(uri, fragment)
    fragment.decode_json_schema(text)
    return fragment


def parse_library(raml: Raml, uri: str) -> Library:
    fragment = parse_fragment(raml, uri, FragmentKind.LIBRARY)
    if not isinstance(fragment, Library):  # pragma: no cover - the kind check guarantees this
        raise RamlError.new('expected a library', uri, kind=ErrorKind.PARSING)
    return fragment

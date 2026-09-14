"""`DomainLocation` — the places an annotation can be applied.

Spec vocabulary rather than annotation machinery, which is why it is a leaf
module of its own: `registry.py` carries the current location on its `ParseCtx`
stack, and `parser/annotations.py` reads it when it builds a `DomainExtension`.
Either module owning the enum would invert a layering direction
(docs/02-architecture.md section 2).

The string values are the spec's own target names, because `allowedTargets:` is
written in those terms and the comparison in P10 should be against the enum's
value with no translation table in between.

See docs/09-security-and-annotations.md section B5.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ['DomainLocation']


class DomainLocation(StrEnum):
    """Where an annotation was written, recorded at the site that builds it.

    `allowedTargets:` on an annotation type names the subset of these that the
    annotation may be applied to; P10 enforces it. Six of the seventeen are
    reachable today — the rest belong to endpoints, templates and security
    schemes, and are declared now so the phases that build those sites pass an
    argument rather than extend an enum.
    """

    API = 'API'
    DOCUMENTATION_ITEM = 'DocumentationItem'
    RESOURCE = 'Resource'
    METHOD = 'Method'
    RESPONSE = 'Response'
    REQUEST_BODY = 'RequestBody'
    RESPONSE_BODY = 'ResponseBody'
    TYPE_DECLARATION = 'TypeDeclaration'
    EXAMPLE = 'Example'
    RESOURCE_TYPE = 'ResourceType'
    TRAIT = 'Trait'
    SECURITY_SCHEME = 'SecurityScheme'
    SECURITY_SCHEME_SETTINGS = 'SecuritySchemeSettings'
    ANNOTATION_TYPE = 'AnnotationType'
    LIBRARY = 'Library'
    OVERLAY = 'Overlay'
    EXTENSION = 'Extension'

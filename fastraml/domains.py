"""`DomainLocation`: the RAML targets an annotation can be applied to.

A leaf module because both `registry.py` (which carries the current target on
`ParseCtx`) and `parser/annotations.py` (which records it on a
`DomainExtension`) need it, and `registry.py` imports no parser module.

The values are the spec's target names, so P10 compares `allowedTargets:`
entries against them directly. See docs/09-security-and-annotations.md § B4.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ['DomainLocation']


class DomainLocation(StrEnum):
    """Where an annotation was written, recorded at the site that builds it.

    `allowedTargets:` on an annotation type names the subset of these that the
    annotation may be applied to; P10 enforces it. `OVERLAY` and `EXTENSION`
    exist for completeness: those fragment kinds are rejected before decoding.
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

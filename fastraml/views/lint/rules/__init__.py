"""The built-in lint rules and their named rulesets."""

from __future__ import annotations

from fastraml.views.lint.engine import Registry, Rule
from fastraml.views.lint.rules.content import NoAmbiguousPaths
from fastraml.views.lint.rules.document import UnusedTrait, UnusedType
from fastraml.views.lint.rules.headers import (
    ContentTypeHeader,
    DuplicateHeader,
    HeaderFieldName,
    HopByHopHeader,
    HttpDateHeader,
)
from fastraml.views.lint.rules.http import (
    AllowHeader405,
    ContentRangeHeader,
    NoContentBody,
    NotModifiedHeaders,
    ObsoleteStatusCode,
    ProxyAuthenticate407,
    RedirectLocation,
    UnreachableStatus,
    WwwAuthenticate401,
)
from fastraml.views.lint.rules.media import DuplicateMediaType, JsonCharset
from fastraml.views.lint.rules.operations import MeaninglessRequestBody, UnsecuredOperation
from fastraml.views.lint.rules.problems import ProblemMediaType, ProblemMemberTypes, ProblemStatus
from fastraml.views.lint.rules.schema import (
    DeprecatedSchemas,
    DiscriminatorWithoutSubtypes,
    JsonRefSiblings,
    MeaninglessMediaTypeSchema,
    MultipleInheritance,
    OptionalAndNil,
    UntypedPayload,
)
from fastraml.views.lint.rules.security import (
    BoundedAdditionalProperties,
    BoundedArray,
    BoundedFile,
    BoundedInteger,
    BoundedNumber,
    CredentialInQuery,
    HttpsOnly,
    InsecureBasicAuthentication,
    IntegerFormat,
    NestedQuantifierPattern,
    NoAdditionalProperties,
    NumericResourceId,
    OAuth1Scheme,
    OAuth2InsecureGrant,
    OAuthEndpointHttps,
    RateLimitHeaders,
    Required401Response,
    Required429Response,
    Required500Response,
    RestrictedFileTypes,
    RestrictedRequestMediaType,
    RestrictedString,
    RetryAfter429,
    UnanchoredStringPattern,
    UnboundedString,
    ValidationErrorResponse,
)
from fastraml.views.lint.rules.style import (
    AvoidExplicitInferredType,
    ExplicitUriParameter,
    MissingDescription,
    MissingDisplayName,
    MissingExample,
    PreferArrayExpression,
    PreferInlineAlias,
    PreferOptionalProperty,
    PreferOptionalType,
    RequireClosedObject,
    UnanchoredPatternProperty,
    UnconstrainedPatternProperty,
    UniqueItemsDiscouraged,
)
from fastraml.views.lint.rules.uris import BaseUriUserinfo, DotSegmentPath, UriPathCharacters

__all__ = ['builtin_registry']


def builtin_registry() -> Registry:
    registry = Registry()
    spec_rules: tuple[Rule, ...] = (
        DeprecatedSchemas(),
        JsonRefSiblings(),
        MeaninglessMediaTypeSchema(),
        MeaninglessRequestBody(),
        NoAmbiguousPaths(),
        UntypedPayload(),
        UnusedTrait(),
        UnusedType(),
    )
    for rule in spec_rules:
        registry.add(rule, sets=('spec', 'recommended'))
    security_rules: tuple[Rule, ...] = (
        BaseUriUserinfo(),
        BoundedAdditionalProperties(),
        BoundedArray(),
        BoundedFile(),
        BoundedInteger(),
        BoundedNumber(),
        CredentialInQuery(),
        HttpsOnly(),
        InsecureBasicAuthentication(),
        IntegerFormat(),
        NestedQuantifierPattern(),
        NoAdditionalProperties(),
        NumericResourceId(),
        OAuth1Scheme(),
        OAuth2InsecureGrant(),
        OAuthEndpointHttps(),
        RateLimitHeaders(),
        Required401Response(),
        Required429Response(),
        Required500Response(),
        RestrictedFileTypes(),
        RestrictedRequestMediaType(),
        RestrictedString(),
        RetryAfter429(),
        UnanchoredStringPattern(),
        UnboundedString(),
        UnsecuredOperation(),
        ValidationErrorResponse(),
    )
    for rule in security_rules:
        registry.add(rule, sets=('security',))
    http_rules: tuple[Rule, ...] = (
        AllowHeader405(),
        ContentRangeHeader(),
        ContentTypeHeader(),
        DotSegmentPath(),
        DuplicateHeader(),
        DuplicateMediaType(),
        HeaderFieldName(),
        HopByHopHeader(),
        HttpDateHeader(),
        JsonCharset(),
        NoContentBody(),
        NotModifiedHeaders(),
        ObsoleteStatusCode(),
        ProxyAuthenticate407(),
        RedirectLocation(),
        UnreachableStatus(),
        UriPathCharacters(),
        WwwAuthenticate401(),
    )
    for rule in http_rules:
        registry.add(rule, sets=('http',))
    problem_rules: tuple[Rule, ...] = (ProblemMediaType(), ProblemMemberTypes(), ProblemStatus())
    for rule in problem_rules:
        registry.add(rule, sets=('problem-details',))
    style_rules: tuple[Rule, ...] = (
        AvoidExplicitInferredType(),
        DiscriminatorWithoutSubtypes(),
        ExplicitUriParameter(),
        MissingDescription(),
        MissingDisplayName(),
        MissingExample(),
        MultipleInheritance(),
        OptionalAndNil(),
        PreferArrayExpression(),
        PreferInlineAlias(),
        PreferOptionalProperty(),
        PreferOptionalType(),
        RequireClosedObject(),
        UnanchoredPatternProperty(),
        UnconstrainedPatternProperty(),
        UniqueItemsDiscouraged(),
    )
    for rule in style_rules:
        registry.add(rule, sets=('style',))
    return registry

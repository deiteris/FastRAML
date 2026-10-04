"""RAML field names shared by fragment, endpoint, and type decoders.

These are wire keys, not Python model attribute names or diagnostic info keys.
`chomp_optional` is the one spelling rule two of them share: a property name
and a resource type's method key both mark themselves optional with `?`.
"""

from typing import Final

FACET_FORMAT: Final = 'format'
FACET_ENUM: Final = 'enum'
FACET_MINIMUM: Final = 'minimum'
FACET_MAXIMUM: Final = 'maximum'
FACET_MULTIPLE_OF: Final = 'multipleOf'
FACET_MIN_LENGTH: Final = 'minLength'
FACET_MAX_LENGTH: Final = 'maxLength'
FACET_PATTERN: Final = 'pattern'
FACET_FILE_TYPES: Final = 'fileTypes'
FACET_ADDITIONAL_PROPERTIES: Final = 'additionalProperties'
FACET_PROPERTIES: Final = 'properties'
FACET_MIN_PROPERTIES: Final = 'minProperties'
FACET_MAX_PROPERTIES: Final = 'maxProperties'
FACET_ITEMS: Final = 'items'
FACET_ANY_OF: Final = 'anyOf'
FACET_MIN_ITEMS: Final = 'minItems'
FACET_MAX_ITEMS: Final = 'maxItems'
FACET_UNIQUE_ITEMS: Final = 'uniqueItems'
FACET_DISCRIMINATOR: Final = 'discriminator'
FACET_DISCRIMINATOR_VALUE: Final = 'discriminatorValue'
FACET_VALUE: Final = 'value'
FACET_DESCRIPTION: Final = 'description'
FACET_DISPLAY_NAME: Final = 'displayName'
FACET_STRICT: Final = 'strict'
FACET_REQUIRED: Final = 'required'
FACET_TYPE: Final = 'type'
FACET_FACETS: Final = 'facets'
FACET_EXAMPLE: Final = 'example'
FACET_EXAMPLES: Final = 'examples'
FACET_DEFAULT: Final = 'default'
FACET_ALLOWED_TARGETS: Final = 'allowedTargets'
FACET_SCHEMA: Final = 'schema'
FACET_HEADERS: Final = 'headers'
FACET_QUERY_PARAMETERS: Final = 'queryParameters'
FACET_QUERY_STRING: Final = 'queryString'
FACET_RESPONSES: Final = 'responses'
FACET_BODY: Final = 'body'
FACET_PROTOCOLS: Final = 'protocols'
FACET_SECURED_BY: Final = 'securedBy'
FACET_IS: Final = 'is'
FACET_TRAITS: Final = 'traits'
FACET_RESOURCE_TYPES: Final = 'resourceTypes'
FACET_USES: Final = 'uses'
FACET_URI_PARAMETERS: Final = 'uriParameters'
FACET_XML: Final = 'xml'
FACET_TITLE: Final = 'title'
FACET_USAGE: Final = 'usage'
FACET_VERSION: Final = 'version'
FACET_BASE_URI: Final = 'baseUri'
FACET_BASE_URI_PARAMETERS: Final = 'baseUriParameters'
FACET_MEDIA_TYPE: Final = 'mediaType'
FACET_DOCUMENTATION: Final = 'documentation'
FACET_TYPES: Final = 'types'
FACET_SCHEMAS: Final = 'schemas'
FACET_ANNOTATION_TYPES: Final = 'annotationTypes'
FACET_SECURITY_SCHEMES: Final = 'securitySchemes'
FACET_DESCRIBED_BY: Final = 'describedBy'
FACET_SETTINGS: Final = 'settings'
FACET_CONTENT: Final = 'content'
FACET_AUTHORIZATION_URI: Final = 'authorizationUri'
FACET_REQUEST_TOKEN_URI: Final = 'requestTokenUri'  # noqa: S105 - a RAML settings key
FACET_TOKEN_CREDENTIALS_URI: Final = 'tokenCredentialsUri'  # noqa: S105 - a RAML settings key
FACET_SIGNATURES: Final = 'signatures'
FACET_ACCESS_TOKEN_URI: Final = 'accessTokenUri'  # noqa: S105 - a RAML settings key
FACET_AUTHORIZATION_GRANTS: Final = 'authorizationGrants'
FACET_SCOPES: Final = 'scopes'


def chomp_optional(name: str) -> tuple[str, bool]:
    """Strip **one** trailing `?`, reporting whether there was one.

    Exactly one: `name??` is the optional property `name?` (docs/05 § 4).
    """
    if name.endswith('?'):
        return name[:-1], True
    return name, False

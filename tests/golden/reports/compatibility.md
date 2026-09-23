# API compatibility

> [!CAUTION]
> **Breaking.** 22 breaking changes require action before release.

| Impact | Changes |
|---|---:|
| Breaking | 22 |
| Review required | 6 |
| Compatible | 18 |
| Documentation | 2 |

## How to read this

Each operation is split into what a caller **sends** and what it **receives**,
then into what was removed, changed and added on that side. Worst first.

| Section | Request | Response |
|---|---|---|
| **Removed** | the API no longer reads it | the API no longer returns it |
| **Changed** | the old form may now be rejected | a value you did not expect may arrive |
| **Added** | new, and breaking only if required | returned as well; ignore it and nothing breaks |

A row names **Where** the change is, and a **Path** when it reaches inside a
shape. The value column is headed by what it holds -- **Type**, **Value**,
**Scheme** -- or **Detail** where a table mixes them, in which case a **What**
column says which each row is. A change states `old -> new`. Columns a table
has no use for are left out of it.

## Every operation

Declared once at the API root, so these reach every operation.

### Request

**Changed**

| Where | Change | Detail | Compatibility |
|---|---|---|---|
| Transport | `baseUri` | `https://{tenant}.old.example.test` -> `https://{tenant}.new.example.test` | Breaking |
| baseUri parameter `tenant` | `maxLength` | `20` -> `10` | Breaking |
| Transport | `protocols` | `HTTP`, `HTTPS` -> `HTTPS` | Breaking |

## Operations added and removed

### Removed

- `GET /removed` - **Read legacy record** - Returns a record through the legacy API.

### Added

- `GET /added` - **Read current record** - Returns a record through the current API.

## `POST /request-required`

### Request

**Changed**

| Where | Path | Change | Detail | Compatibility |
|---|---|---|---|---|
| Body `application/json` | `$.email` | Requiredness | Optional -> Required | Breaking |

## `POST /request-optional`

### Request

**Changed**

| Where | Path | Change | Detail | Compatibility |
|---|---|---|---|---|
| Body `application/json` | `$.note` | Requiredness | Required -> Optional | Compatible |

## `POST /request-add-required`

### Request

**Added**

| Where | Path | Type | Compatibility |
|---|---|---|---|
| Body `application/json` | `$.email` | required `string` | Breaking |

## `POST /request-add-optional`

### Request

**Added**

| Where | Path | Type | Description | Compatibility |
|---|---|---|---|---|
| Body `application/json` | `$.marketingCode` | optional `string` | Campaign this order came from. Ignored when absent. | Compatible |

## `POST /request-remove`

### Request

**Removed**

| Where | Path | Type | Compatibility |
|---|---|---|---|
| Body `application/json` | `$.legacyCode` | optional `string` | Review |

## `POST /request-tight`

### Request

**Changed**

| Where | Path | Change | Detail | Compatibility |
|---|---|---|---|---|
| Body `application/json` | `$.profile.nickname` | `maxLength` | `10` -> `5` | Breaking |

## `POST /request-loose`

### Request

**Changed**

| Where | Path | Change | Detail | Compatibility |
|---|---|---|---|---|
| Body `application/json` | `$.search.query` | `maxLength` | `5` -> `10` | Compatible |

## `POST /request-enum-remove`

### Request

**Removed**

| Where | Path | Value | Compatibility |
|---|---|---|---|
| Body `application/json` | `$.filters.state` | `archived` | Breaking |

## `POST /request-enum-add`

### Request

**Added**

| Where | Path | Value | Compatibility |
|---|---|---|---|
| Body `application/json` | `$.filters.state` | `archived` | Compatible |

## `GET /response-remove`

### Response

**Removed**

| Where | Path | Type | Compatibility |
|---|---|---|---|
| `200` body `application/json` | `$.legacyLabel` | optional `string` | Breaking |

## `GET /response-optional`

### Response

**Changed**

| Where | Path | Change | Detail | Compatibility |
|---|---|---|---|---|
| `200` body `application/json` | `$.displayName` | Requiredness | Required -> Optional | Breaking |

## `GET /response-required`

### Response

**Changed**

| Where | Path | Change | Detail | Compatibility |
|---|---|---|---|---|
| `200` body `application/json` | `$.identifier` | Requiredness | Optional -> Required | Compatible |

## `GET /response-add`

### Response

**Added**

| Where | Path | Type | Description | Compatibility |
|---|---|---|---|---|
| `200` body `application/json` | `$.traceId` | optional `string` | Correlates this response with a server-side request log. | Compatible |

## `GET /response-loose`

### Response

**Changed**

| Where | Path | Change | Detail | Compatibility |
|---|---|---|---|---|
| `200` body `application/json` | `$.result.summary` | `maxLength` | `5` -> `10` | Breaking |

## `GET /response-tight`

### Response

**Changed**

| Where | Path | Change | Detail | Compatibility |
|---|---|---|---|---|
| `200` body `application/json` | `$.result.summary` | `maxLength` | `10` -> `5` | Compatible |

## `GET /response-enum-add`

### Response

**Added**

| Where | Path | Value | Compatibility |
|---|---|---|---|
| `200` body `application/json` | `$.records[].state` | `archived` | Review |

## `GET /response-enum-remove`

### Response

**Removed**

| Where | Path | Value | Compatibility |
|---|---|---|---|
| `200` body `application/json` | `$.records[].state` | `archived` | Compatible |

## `GET /security-added`

### Request

**Changed**

| Where | Change | Detail | Compatibility |
|---|---|---|---|
| Security | Authentication | Optional -> Required | Breaking |

## `GET /security-removed`

### Request

**Changed**

| Where | Change | Detail | Compatibility |
|---|---|---|---|
| Security | Authentication | Required -> Optional | Compatible |

## `GET /alternative-added`

### Request

**Added**

| Where | Scheme | Description | Compatibility |
|---|---|---|---|
| Security | `apiKey` | Per-tenant key, issued from the console. | Compatible |

## `GET /alternative-removed`

### Request

**Removed**

| Where | Scheme | Compatibility |
|---|---|---|
| Security | `apiKey` | Breaking |

## `GET /settings`

### Request

**Changed**

| Where | Change | Detail | Compatibility |
|---|---|---|---|
| Security | `accessTokenUri` | `https://auth.example.test/token` -> `https://auth.example.test/v2/token` | Review |

## `GET /protocol-added`

### Request

**Changed**

| Where | Change | Detail | Compatibility |
|---|---|---|---|
| Transport | `protocols` | `HTTP` -> `HTTP`, `HTTPS` | Compatible |

## `POST /type-change`

### Request

**Changed**

| Where | Path | Change | Detail | Compatibility |
|---|---|---|---|---|
| Body `application/json` | `$.identifier` | `type` | `string` -> `integer` | Breaking |

## `GET /format-change`

### Response

**Changed**

| Where | Path | Change | Detail | Compatibility |
|---|---|---|---|---|
| `200` body `application/json` | `$.generatedAt` | `format` | `rfc3339` -> `rfc2616` | Breaking |

## `GET /other-change`

### Response

**Changed**

| Where | Path | Change | Detail | Compatibility |
|---|---|---|---|---|
| `200` body `application/json` | `$.productCode` | `pattern` | `^[A-Z]+$` -> `^[a-z]+$` | Review |

## `GET /documentation`

### Documentation

**Changed**

| Where | Change | Detail | Compatibility |
|---|---|---|---|
| Operation | `description` | Old operation documentation -> New operation documentation | Cosmetic |

## `GET /status-removed`

### Response

**Removed**

| Where | Description | Compatibility |
|---|---|---|
| Status `410` | The record is permanently gone. | Breaking |

## `GET /status-added`

### Response

**Added**

| Where | Description | Compatibility |
|---|---|---|
| Status `202` | The request was accepted for processing. | Compatible |

## `POST /media-types`

### Request

**Removed**

| Where | Compatibility |
|---|---|
| Body `application/vnd.legacy+json` | Breaking |

**Added**

| Where | Compatibility |
|---|---|
| Body `application/vnd.example+json` | Compatible |

### Response

**Removed**

| Where | Compatibility |
|---|---|
| `200` body `application/problem+json` | Breaking |

**Added**

| Where | Compatibility |
|---|---|
| `200` body `application/vnd.example+json` | Compatible |

## `GET /parameters`

### Request

**Changed**

| Where | Change | Detail | Compatibility |
|---|---|---|---|
| query parameter `limit` | Requiredness | Optional -> Required | Breaking |
| query parameter `limit` | `type` | `string` -> `integer` | Breaking |

**Removed**

| Where | Type | Compatibility |
|---|---|---|
| query parameter `legacy` | optional `string` | Review |

**Added**

| Where | Type | Description | Compatibility |
|---|---|---|---|
| query parameter `cursor` | optional `string` | Opaque position from the previous page's Link header. | Compatible |

### Response

**Removed**

| Where | Type | Compatibility |
|---|---|---|
| `200` header `X-Legacy` | optional `string` | Breaking |

**Added**

| Where | Type | Compatibility |
|---|---|---|
| `200` header `X-Trace` | optional `string` | Compatible |

## `GET /response-documentation`

### Response

**Changed**

| Where | Change | Detail | Compatibility |
|---|---|---|---|
| Status `200` | `description` | Old response documentation -> New response documentation | Cosmetic |

## `GET /union-members`

### Response

**Added**

| Where | Path | Compatibility |
|---|---|---|
| `200` body `application/json` | `$.result<boolean>` | Review |

**Removed**

| Where | Path | Compatibility |
|---|---|---|
| `200` body `application/json` | `$.result<integer>` | Compatible |

## `GET /scopes`

### Request

**Changed**

| Where | Change | Detail | Compatibility |
|---|---|---|---|
| Security | `scopes` | `read` -> `read`, `write` | Breaking |

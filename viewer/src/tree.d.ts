/**
 * The `fastraml tree` contract.
 *
 * Assembled by `python -m fastraml.views.bindings typescript`. Do not edit the
 * assembled file. Edit `fastraml/views/bindings/static/tree.d.ts`, which holds
 * the metamodel aliases and the fixed records; everything from `ShapeType` on
 * is generated from `fastraml/views/tree.py` and the kind classes in
 * `fastraml/types/`, so a facet added to a kind arrives without either half
 * being edited. `tests/unit/test_bindings.py` fails when they disagree.
 *
 * The metamodel is three constructs (docs/16-graph.md § 6.1):
 *
 *   {"$ref": <address>}                            a link -- look the target up
 *   {"type": "recursive", "head": {"$ref": ...}}   repeats here, do not expand
 *   anything else                                  containment -- descend
 *
 * A consumer descends containment, follows a link when it chooses to, and stops
 * at a recursion marker. It maintains no ancestor set. Requires
 * `ParseOptions(unwrap=True)`, which `fastraml tree` uses.
 */

/** A structural address: stable across re-parses, and the identity of a node. */
export type Address = string;

/** An exact decimal carried as text so no consumer rounds it through a double. */
export type ExactDecimal = string;

export interface JsonObject {
  [key: string]: Json;
}

export type Json = string | number | boolean | null | Json[] | JsonObject;

export type Protocol = 'HTTP' | 'HTTPS';
export type ParameterBinding = 'uri' | 'query' | 'header';
export type SecuritySchemeType =
  | 'null'
  | 'OAuth 1.0'
  | 'OAuth 2.0'
  | 'Basic Authentication'
  | 'Digest Authentication'
  | 'Pass Through'
  | `x-${string}`;

export type SourceFile = string;
export type DeclarationName = string;
export type EndpointPath = string;
export type StatusCode = string;
export type MediaType = string;

/** A link. Its sole key is the test -- an expanded shape carries `id` as well. */
export interface Ref {
  $ref: Address;
}

export type ShapeNode = Shape | Ref | Recursion;
export type ShapeDeclarations = Record<DeclarationName, Shape | Ref>;
export type ShapeDeclarationsByFile = Record<SourceFile, ShapeDeclarations>;
export type SecuritySchemeDeclarations = Record<DeclarationName, SecurityScheme>;
export type SecuritySchemeDeclarationsByFile = Record<SourceFile, SecuritySchemeDeclarations>;
export type EndpointsByPath = Record<EndpointPath, Endpoint>;
export type OperationsByMethod = Partial<Record<HttpMethod, Operation>>;
export type ResponsesByStatus = Record<StatusCode, Response>;
export type BodiesByMediaType = Record<MediaType, ShapeNode | null>;
export type SecuritySetting = string | string[];
export type SecuritySettings = Record<string, SecuritySetting>;

export interface DocumentationItem {
  title: string;
  content: string;
}

export interface Property {
  required: boolean;
  type: ShapeNode | null;
}

export interface PatternProperty {
  pattern: string;
  type: ShapeNode | null;
}

export interface Parameter {
  binding: ParameterBinding;
  required: boolean;
  type: ShapeNode | null;
}

export type ShapeType = 'any' | 'nil' | 'null' | 'boolean' | 'string' | 'integer' | 'number' | 'datetime' | 'datetime-only' | 'date-only' | 'time-only' | 'file' | 'object' | 'array' | 'union' | 'json';
export type HttpMethod = 'connect' | 'delete' | 'get' | 'head' | 'options' | 'patch' | 'post' | 'put' | 'trace';
export type FragmentKind = 'API' | 'Library' | 'DataType' | 'AnnotationTypeDeclaration' | 'NamedExample' | 'DocumentationItem' | 'Trait' | 'ResourceType' | 'SecurityScheme';
export type AnnotationTarget = 'API' | 'DocumentationItem' | 'Resource' | 'Method' | 'Response' | 'RequestBody' | 'ResponseBody' | 'TypeDeclaration' | 'Example' | 'ResourceType' | 'Trait' | 'SecurityScheme' | 'SecuritySchemeSettings' | 'AnnotationType' | 'Library' | 'Overlay' | 'Extension';
/**
 * Fields shared by every expanded type. Kind-specific facets live on the
 * discriminated interfaces below.
 *
 * Numeric bounds use `ExactDecimal`; counts such as `max_items` remain
 * numbers because they are bounded by memory rather than numeric precision.
 */
export interface ShapeBase {
  id: Address | null;
  name: string | null;
  inherits?: ShapeNode[];
  custom_facets?: Record<string, Json>;
  declared_facets?: Record<string, Property>;
  annotations?: Applied[];
  type_expr?: string;
  display_name?: string;
  description?: string;
  required?: boolean;
  default?: Json;
  example?: Example;
  examples?: Record<string, Example>;
  enum?: Json[];
  xml?: Json;
  allowed_targets?: AnnotationTarget[];
}

export interface AnyShape extends ShapeBase {
  type: 'any';
}

export interface NilShape extends ShapeBase {
  type: 'nil' | 'null';
}

export interface BooleanShape extends ShapeBase {
  type: 'boolean';
}

export interface StringShape extends ShapeBase {
  type: 'string';
  max_length?: number;
  min_length?: number;
  pattern?: string;
}

export interface IntegerShape extends ShapeBase {
  type: 'integer';
  format?: string;
  maximum?: ExactDecimal; // exact decimal, e.g. "0.01" or "1.7976931348623157E+308"
  minimum?: ExactDecimal; // exact decimal, e.g. "0.01" or "1.7976931348623157E+308"
  multiple_of?: ExactDecimal; // exact decimal, e.g. "0.01" or "1.7976931348623157E+308"
}

export interface NumberShape extends ShapeBase {
  type: 'number';
  format?: string;
  maximum?: ExactDecimal; // exact decimal, e.g. "0.01" or "1.7976931348623157E+308"
  minimum?: ExactDecimal; // exact decimal, e.g. "0.01" or "1.7976931348623157E+308"
  multiple_of?: ExactDecimal; // exact decimal, e.g. "0.01" or "1.7976931348623157E+308"
}

export interface DateTimeShape extends ShapeBase {
  type: 'datetime';
  format?: string;
}

export interface DateTimeOnlyShape extends ShapeBase {
  type: 'datetime-only';
}

export interface DateOnlyShape extends ShapeBase {
  type: 'date-only';
}

export interface TimeOnlyShape extends ShapeBase {
  type: 'time-only';
}

export interface FileShape extends ShapeBase {
  type: 'file';
  file_types?: string[];
  max_length?: number;
  min_length?: number;
}

export interface ObjectShape extends ShapeBase {
  type: 'object';
  additional_properties?: boolean;
  discriminator?: string;
  discriminator_value?: Json;
  max_properties?: number;
  min_properties?: number;
  pattern_properties?: Record<string, PatternProperty>;
  properties?: Record<string, Property>;
}

export interface ArrayShape extends ShapeBase {
  type: 'array';
  items?: ShapeNode;
  max_items?: number;
  min_items?: number;
  unique_items?: boolean;
}

export interface UnionShape extends ShapeBase {
  type: 'union';
  any_of?: ShapeNode[];
}

export interface JsonShape extends ShapeBase {
  type: 'json';
  json_schema?: Json;
  projection?: Shape;
}

/**
 * A type that repeats here. Do not expand it; look `head` up instead.
 *
 * Spelled in `type` rather than a key of its own so a consumer that
 * switches on `type` and has not handled it fails loudly.
 */
export interface Recursion extends ShapeBase {
  type: 'recursive';
  head: Ref;
}

export type Shape = AnyShape | NilShape | BooleanShape | StringShape | IntegerShape | NumberShape | DateTimeShape | DateTimeOnlyShape | DateOnlyShape | TimeOnlyShape | FileShape | ObjectShape | ArrayShape | UnionShape | JsonShape;

export interface Document {
  format: 'fastraml-tree';
  format_version: 1;
  view: 'effective';
  base: Address;
  entry_point: EntryPoint | null;
  types: ShapeDeclarationsByFile;
  annotation_types: ShapeDeclarationsByFile;
  security_schemes: SecuritySchemeDeclarationsByFile;
  endpoints: EndpointsByPath;
  annotations: DocumentAnnotation[];
}

export interface EntryPoint {
  kind: FragmentKind;
  base_uri_parameters?: Record<string, Parameter>;
  documentation?: DocumentationItem[];
  secured_by?: SecuredBy[];
  annotations?: Applied[];
  title?: string;
  version?: string;
  base_uri?: string;
  media_types?: string[];
  protocols?: Protocol[];
  usage?: string;
  description?: string;
}

export interface SecurityScheme {
  id: Address | null;
  name: string;
  type: SecuritySchemeType;
  described_by?: DescribedBy;
  annotations?: Applied[];
  display_name?: string;
  description?: string;
  settings?: SecuritySettings;
}

export interface DescribedBy {
  query_string?: ShapeNode | null;
  responses?: ResponsesByStatus;
  headers?: Record<string, Parameter>;
  query_parameters?: Record<string, Parameter>;
}

export interface Endpoint {
  id: Address | null;
  operations: OperationsByMethod;
  secured_by: SecuredBy[];
  uri_parameters?: Record<string, Parameter>;
  annotations?: Applied[];
  display_name?: string;
  description?: string;
}

export interface Operation {
  id: Address | null;
  responses: ResponsesByStatus;
  description?: string;
  display_name?: string;
  protocols?: Protocol[];
  secured_by?: SecuredBy[];
  annotations?: Applied[];
  query_string?: ShapeNode | null;
  bodies?: BodiesByMediaType;
  headers?: Record<string, Parameter>;
  query_parameters?: Record<string, Parameter>;
}

export interface Response {
  description?: string;
  headers?: Record<string, Parameter>;
  bodies?: BodiesByMediaType;
  annotations?: Applied[];
}

export interface SecuredBy {
  name: string;
  is_null: boolean;
  bound: boolean;
  declaration: Address | null;
  scopes: string[] | null;
}

export interface Applied {
  name: string;
  type: Address | null;
  value: Json;
}

export interface DocumentAnnotation {
  name: string;
  target: AnnotationTarget;
  type: Address | null;
  value: Json;
}

export interface Example {
  value: Json;
  annotations?: Applied[];
  display_name?: string;
  description?: string;
  strict?: boolean;
}

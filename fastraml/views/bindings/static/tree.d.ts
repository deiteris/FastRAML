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
 * The metamodel is three constructs (docs/16-graph.md section 11.10):
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

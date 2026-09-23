// Package tree is the `fastraml tree` contract.
//
// Assembled by `python -m fastraml.views.bindings golang`. Do not edit the
// assembled file. Edit `fastraml/views/bindings/static/tree.go`, which holds
// the metamodel types and their decoding; everything from `ShapeType` on is
// generated from `fastraml/views/tree.py` and the kind classes in
// `fastraml/types/`, so a facet added to a kind arrives without either half
// being edited. `tests/unit/test_bindings.py` fails when they disagree.
//
// The metamodel is three constructs (docs/16-graph.md § 6.1):
//
//	{"$ref": <address>}                            a link -- look the target up
//	{"type": "recursive", "head": {"$ref": ...}}   repeats here, do not expand
//	anything else                                  containment -- descend
//
// A consumer descends containment, follows a link when it chooses to, and stops
// at a recursion marker. It maintains no ancestor set. Requires
// ParseOptions(unwrap=True), which `fastraml tree` uses.
//
// Every map whose keys are data keeps its insertion order. Declaration order is
// preserved everywhere the model is exposed, so a plain Go map would discard a
// fact the tree carries -- and discard it without failing, which is the worse
// half. That is the one dependency beyond the standard library:
//
//	go get github.com/wk8/go-ordered-map/v2
//
// # Requires Go 1.24
//
// An optional key is tagged `,omitzero`, which is Go 1.24. `omitempty` differs
// from it on exactly the case the tree produces -- an empty list -- and would
// write a `[]` that arrived back as an absent key. Put `go 1.24` in your go.mod:
// an older toolchain then refuses to build, where it would otherwise ignore the
// tag and encode wrongly without saying so.
//
// With that floor, decoding and encoding are both exact: every key that arrived
// is written back, an absent key stays absent, and a null stays a null.
package tree

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"

	orderedmap "github.com/wk8/go-ordered-map/v2"
)

// RecursionType is the value of a recursion marker's `type`, and the only thing
// that tells one apart from a shape.
const RecursionType = "recursive"

// Address is a structural address: stable across re-parses, and the identity of
// a node.
type Address = string

// ExactDecimal is an exact decimal carried as text so no consumer rounds it
// through a float64.
type ExactDecimal = string

// Json is one arbitrary JSON value -- author data, so it is whatever the author
// wrote. It stays raw rather than decoding to `any`: raw bytes keep the object
// key order the tree went to trouble to preserve, and keep an absent key
// distinguishable from a null one. A nil Json is the absent key; a Json holding
// `null` is a value the author wrote.
//
// It is `json.RawMessage` with three methods, and it is a defined type rather
// than an alias so that it can have them.
type Json []byte

// MarshalJSON implements json.Marshaler.
func (j Json) MarshalJSON() ([]byte, error) {
	if j == nil {
		return []byte("null"), nil
	}
	return j, nil
}

// UnmarshalJSON implements json.Unmarshaler.
func (j *Json) UnmarshalJSON(data []byte) error {
	if j == nil {
		return errors.New("tree.Json: UnmarshalJSON on nil pointer")
	}
	*j = append((*j)[0:0], data...)
	return nil
}

// Decode unmarshals the value into v. Nothing decodes a Json on the way in, so
// this is where a consumer that wants it as a Go value asks for it.
func (j Json) Decode(v any) error {
	if len(j) == 0 {
		return errors.New("tree.Json: the key is absent")
	}
	return json.Unmarshal(j, v)
}

// Object decodes the value as a JSON object, in the key order the author wrote.
// This is the reason the value is raw: `map[string]any` would lose that order,
// and lose it without failing.
func (j Json) Object() (JsonObject, error) {
	object := orderedmap.New[string, Json]()
	if err := j.Decode(object); err != nil {
		return nil, err
	}
	return object, nil
}

// IsNull reports whether the author wrote a null. That is not the same as the
// key being absent, which is `j == nil`.
func (j Json) IsNull() bool {
	return bytes.Equal(bytes.TrimSpace(j), []byte("null"))
}

// JsonObject is a Json that is an object, decoded with its key order intact.
type JsonObject = *orderedmap.OrderedMap[string, Json]

// Protocol is a transport an API or operation declares.
type Protocol string

const (
	ProtocolHTTP  Protocol = "HTTP"
	ProtocolHTTPS Protocol = "HTTPS"
)

// ParameterBinding is where a parameter rides.
type ParameterBinding string

const (
	ParameterBindingURI    ParameterBinding = "uri"
	ParameterBindingQuery  ParameterBinding = "query"
	ParameterBindingHeader ParameterBinding = "header"
)

// SecuritySchemeType is the six the spec names, plus `x-<anything>`, which is a
// scheme type the spec allows. The constants below are the closed half; the
// type stays open because the vocabulary is.
type SecuritySchemeType string

const (
	SecuritySchemeTypeNull                 SecuritySchemeType = "null"
	SecuritySchemeTypeOAuth10              SecuritySchemeType = "OAuth 1.0"
	SecuritySchemeTypeOAuth20              SecuritySchemeType = "OAuth 2.0"
	SecuritySchemeTypeBasicAuthentication  SecuritySchemeType = "Basic Authentication"
	SecuritySchemeTypeDigestAuthentication SecuritySchemeType = "Digest Authentication"
	SecuritySchemeTypePassThrough          SecuritySchemeType = "Pass Through"
)

// The semantic key domains. Each is a string, and naming it says what the
// string is a key of.
type (
	SourceFile      = string
	DeclarationName = string
	EndpointPath    = string
	StatusCode      = string
	MediaType       = string
)

// The maps whose keys are data: a file, a declaration name, a path, a method, a
// status code, a media type, a facet name. Ordered, because the tree's order is
// the document's order.
type (
	ShapeDeclarations                = *orderedmap.OrderedMap[DeclarationName, ShapeNode]
	ShapeDeclarationsByFile          = *orderedmap.OrderedMap[SourceFile, ShapeDeclarations]
	SecuritySchemeDeclarations       = *orderedmap.OrderedMap[DeclarationName, SecurityScheme]
	SecuritySchemeDeclarationsByFile = *orderedmap.OrderedMap[SourceFile, SecuritySchemeDeclarations]
	EndpointsByPath                  = *orderedmap.OrderedMap[EndpointPath, Endpoint]
	OperationsByMethod               = *orderedmap.OrderedMap[HttpMethod, Operation]
	ResponsesByStatus                = *orderedmap.OrderedMap[StatusCode, Response]
	BodiesByMediaType                = *orderedmap.OrderedMap[MediaType, *ShapeNode]
	SecuritySettings                 = *orderedmap.OrderedMap[string, SecuritySetting]
	ParametersByName                 = *orderedmap.OrderedMap[string, Parameter]
	ExamplesByName                   = *orderedmap.OrderedMap[string, Example]
	PropertiesByName                 = *orderedmap.OrderedMap[string, Property]
	PatternPropertiesByPattern       = *orderedmap.OrderedMap[string, PatternProperty]
	FacetValuesByName                = *orderedmap.OrderedMap[string, Json]
)

// Ref is a link. Its sole key is the test -- an expanded shape carries `id` as
// well.
type Ref struct {
	Ref Address `json:"$ref"`
}

// SecuritySetting is one settings value. The spec allows a scalar or a list of
// them, so exactly one of the two fields is set.
type SecuritySetting struct {
	Scalar *string
	List   []string
}

// MarshalJSON implements json.Marshaler.
func (s SecuritySetting) MarshalJSON() ([]byte, error) {
	switch {
	case s.List != nil:
		return json.Marshal(s.List)
	case s.Scalar != nil:
		return json.Marshal(*s.Scalar)
	}
	return []byte("null"), nil
}

// UnmarshalJSON implements json.Unmarshaler.
func (s *SecuritySetting) UnmarshalJSON(data []byte) error {
	*s = SecuritySetting{}
	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null")) {
		return nil
	}
	if trimmed[0] == '[' {
		return json.Unmarshal(data, &s.List)
	}
	var scalar string
	if err := json.Unmarshal(data, &scalar); err != nil {
		return err
	}
	s.Scalar = &scalar
	return nil
}

// ShapeNode is one position in the tree that a type can occupy, and so it is
// the metamodel's three constructs: a link, a recursion marker, or an expanded
// shape. Exactly one field is set. Go has no union, so the discrimination the
// contract states is written out once, here.
type ShapeNode struct {
	Ref       *Ref
	Recursion *Recursion
	Shape     Shape
}

// MarshalJSON implements json.Marshaler.
func (n ShapeNode) MarshalJSON() ([]byte, error) {
	switch {
	case n.Ref != nil:
		return json.Marshal(n.Ref)
	case n.Recursion != nil:
		return json.Marshal(n.Recursion)
	case n.Shape != nil:
		return json.Marshal(n.Shape)
	}
	return []byte("null"), nil
}

// UnmarshalJSON implements json.Unmarshaler. A `type` of "recursive" is a
// marker, any other `type` is a shape, and no `type` at all is a link -- which
// is the rule docs/16-graph.md § 6.1 states, and the whole of it. An
// expanded shape always carries `id`, `name` and `type`, so `type` alone
// separates the three and a node carrying neither `type` nor `$ref` is not a
// node of this contract.
//
// One probe, and the shape decode is handed the answer rather than finding it
// again: decoding the node twice to read one key costs a map allocation per
// node, and there are tens of thousands of them in a document of any size.
func (n *ShapeNode) UnmarshalJSON(data []byte) error {
	*n = ShapeNode{}
	if bytes.Equal(bytes.TrimSpace(data), []byte("null")) {
		return nil
	}
	var probe struct {
		Ref  *Address   `json:"$ref"`
		Type *ShapeType `json:"type"`
	}
	if err := json.Unmarshal(data, &probe); err != nil {
		return err
	}
	switch {
	case probe.Type == nil && probe.Ref == nil:
		return fmt.Errorf("tree: node is neither a link nor a shape: %.120s", data)
	case probe.Type == nil:
		n.Ref = &Ref{}
		return json.Unmarshal(data, n.Ref)
	case *probe.Type == RecursionType:
		n.Recursion = &Recursion{}
		return json.Unmarshal(data, n.Recursion)
	}
	shape, err := shapeOf(*probe.Type)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(data, shape); err != nil {
		return err
	}
	n.Shape = shape
	return nil
}

// Shape is one expanded type. Go has no discriminated union, so the union is an
// interface and the discriminator is a method: narrow a Shape with a type
// switch, or ask it its kind. UnmarshalShape decodes one from bytes.
type Shape interface {
	ShapeKind() ShapeType
	Base() *ShapeBase
}

// Base returns the fields every expanded type carries. Declared once here and
// promoted into every variant by embedding, so a walk can read `id` off a Shape
// without a type switch.
func (b *ShapeBase) Base() *ShapeBase { return b }

// UnmarshalShape decodes one expanded type into the struct its `type` names.
func UnmarshalShape(data []byte) (Shape, error) {
	var probe struct {
		Type ShapeType `json:"type"`
	}
	if err := json.Unmarshal(data, &probe); err != nil {
		return nil, err
	}
	shape, err := shapeOf(probe.Type)
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(data, shape); err != nil {
		return nil, err
	}
	return shape, nil
}

// shapeOf is an empty variant of the kind named. The table it reads is
// generated from the parser's own kind table, so `nil` and `null` reach the one
// NilShape exactly as they share an implementation there.
func shapeOf(kind ShapeType) (Shape, error) {
	empty, found := shapeKinds[kind]
	if !found {
		return nil, fmt.Errorf("tree: unknown shape type %q", kind)
	}
	return empty(), nil
}

// DocumentationItem is one entry of the root `documentation:` list.
type DocumentationItem struct {
	Title   string `json:"title"`
	Content string `json:"content"`
}

// Property is one declared property of an object type, or one declared custom facet.
type Property struct {
	Required bool       `json:"required"`
	Type     *ShapeNode `json:"type"`
}

// PatternProperty is one `/regex/` property name and what it admits.
type PatternProperty struct {
	Pattern string     `json:"pattern"`
	Type    *ShapeNode `json:"type"`
}

// Parameter is one URI, query or header parameter.
type Parameter struct {
	Binding  ParameterBinding `json:"binding"`
	Required bool             `json:"required"`
	Type     *ShapeNode       `json:"type"`
}

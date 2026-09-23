// Reading a `fastraml tree` document.
//
// Assembled by `python -m fastraml.views.bindings golang --runtime FILE`. Do not
// edit the assembled file. Edit `fastraml/views/bindings/static/walk.go`, which
// holds the reading; the `childShapes` methods below it are generated from the
// same table the other backends render as data, so a facet that starts holding a
// shape starts being descended without either half being edited.
// `tests/unit/test_bindings.py` fails when they disagree.
//
// The metamodel is three constructs (docs/16-graph.md § 6.1):
//
//	{"$ref": <address>}                            a link -- look the target up
//	{"type": "recursive", "head": {"$ref": ...}}   repeats here, do not expand
//	anything else                                  containment -- descend
//
// A consumer descends containment, follows a link when it chooses to, and stops
// at a recursion marker. It maintains no ancestor set.
//
// This holds only what the contract states. Naming, page routing and path
// nesting belong to the consumer.
//
// Go's walk is code rather than a table: the other two backends index a record
// by a string key at run time, which Go cannot do, so `shape_bearing()` is
// generated as one `childShapes` method per record.
package tree

import "fmt"

// UnreadableTree is returned when the document is not a tree this version of
// the contract understands.
type UnreadableTree struct {
	Format  string
	Version int
	View    string
}

func (e *UnreadableTree) Error() string {
	return fmt.Sprintf(
		"expected %s v%d (%s), got %q v%d (%q) -- regenerate with a matching fastraml",
		Format, FormatVersion, View, e.Format, e.Version, e.View,
	)
}

// Tree is one document with its addresses indexed.
type Tree struct {
	Document *Document
	index    map[Address]Shape
}

// Of checks the envelope, then indexes the document.
//
// A tree from a later format version may have renamed a field a consumer reads.
// Reading it anyway produces output that is wrong rather than absent, so refuse
// anything the three envelope fields do not match (docs/16 § 6).
func Of(document *Document) (*Tree, error) {
	if err := Check(document); err != nil {
		return nil, err
	}
	tree := &Tree{Document: document, index: map[Address]Shape{}}
	for _, shape := range tree.Shapes() {
		if address := shape.Base().ID; address != nil {
			if _, seen := tree.index[*address]; !seen {
				tree.index[*address] = shape
			}
		}
	}
	return tree, nil
}

// Check is the envelope alone, for a caller that wants to refuse a document
// before doing anything with it. Of indexes as well, which is the whole walk; a
// loader that only needs to know whether it can read the file should not pay for
// that and then discard it.
func Check(document *Document) error {
	if document == nil {
		return &UnreadableTree{}
	}
	if document.Format != Format || document.FormatVersion != FormatVersion || document.View != View {
		return &UnreadableTree{Format: document.Format, Version: document.FormatVersion, View: document.View}
	}
	return nil
}

// At is the shape an address names, or nil.
func (t *Tree) At(address Address) Shape {
	return t.index[address]
}

// Resolve follows a link, once.
//
// A recursion marker comes back as itself, in the second return. Treating it as
// a link would break the traversal law: a walker that expands links cannot tell
// a repeat from a fresh subtree.
func (t *Tree) Resolve(node *ShapeNode) (Shape, *Recursion) {
	switch {
	case node == nil:
		return nil, nil
	case node.Recursion != nil:
		return nil, node.Recursion
	case node.Ref != nil:
		return t.index[node.Ref.Ref], nil
	}
	return node.Shape, nil
}

// Content is what a shape is, rather than how it arrived.
//
// A `json` shape carries its schema twice: JsonSchema in JSON Schema's own
// vocabulary, and Projection as the nearest RAML shape. The projection is the
// type (docs/16 § 6.2); the `json` shape itself carries no RAML properties or
// facets.
func (t *Tree) Content(shape Shape) Shape {
	schema, ok := shape.(*JsonShape)
	if !ok || schema.Projection == nil || schema.Projection.Shape == nil {
		return shape
	}
	return schema.Projection.Shape
}

// Children is the shape nodes directly under a node.
//
// A link and a recursion marker have none: both are stops. Neither is followed
// here, so the walk needs no visited set.
func (t *Tree) Children(node *ShapeNode) []*ShapeNode {
	if node == nil || node.Shape == nil {
		return nil
	}
	return shapeChildren(node.Shape, nil)
}

// Shapes is every expanded shape in the document, by containment.
//
// No ancestor set and no visited set. Containment is a tree, and the two
// constructs that would make it a graph are both stopped at.
func (t *Tree) Shapes() []Shape {
	var out []Shape
	for _, node := range t.Document.childShapes(nil) {
		out = expand(node, out)
	}
	return out
}

func expand(node *ShapeNode, out []Shape) []Shape {
	if node == nil || node.Shape == nil {
		return out
	}
	out = append(out, node.Shape)
	for _, child := range shapeChildren(node.Shape, nil) {
		out = expand(child, out)
	}
	return out
}

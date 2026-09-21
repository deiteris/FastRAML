// Command conform answers the conformance corpus through Go's reading of the
// tree.
//
// Copied verbatim by `python -m fastraml.views.bindings golang --conform FILE`
// apart from its import path. Do not edit the copy. Edit
// `fastraml/views/bindings/static/conform.go`.
//
//	go run ./conform <corpus-directory>
//
// Reads `cases.json`, runs `walk.go` over each tree it names, and writes the
// answers to stdout as JSON. It holds no expectations: the corpus has those, and
// `tests/unit/test_conformance.py` does the comparing.
//
// `conform.py` and `conform.ts` are the same file. Each answer is a string or
// a list of strings.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"conformance/tree"
)

type caseFile struct {
	Cases map[string]struct {
		Tree     string `json:"tree"`
		Expected struct {
			Probes []string `json:"probes"`
		} `json:"expected"`
	} `json:"cases"`
	Refuse []json.RawMessage `json:"refuse"`
}

type result struct {
	Shapes   []*string           `json:"shapes"`
	Probes   []string            `json:"probes"`
	Children map[string][]string `json:"children"`
	Resolve  map[string]*string  `json:"resolve"`
	Content  map[string]*string  `json:"content"`
}

func label(node *tree.ShapeNode) string {
	switch {
	case node == nil:
		return "none"
	case node.Ref != nil:
		return "ref:" + node.Ref.Ref
	case node.Recursion != nil:
		return "recursive:" + deref(node.Recursion.ID)
	case node.Shape != nil:
		return "shape:" + deref(node.Shape.Base().ID)
	}
	return "none"
}

func deref(address *tree.Address) string {
	if address == nil {
		return ""
	}
	return *address
}

func answers(document *tree.Document, probes []string) (result, error) {
	t, err := tree.Of(document)
	if err != nil {
		return result{}, err
	}
	out := result{
		Shapes:   []*string{},
		Probes:   probes,
		Children: map[string][]string{},
		Resolve:  map[string]*string{},
		Content:  map[string]*string{},
	}
	for _, shape := range t.Shapes() {
		out.Shapes = append(out.Shapes, shape.Base().ID)
	}
	for _, address := range probes {
		children := []string{}
		if found := t.At(address); found != nil {
			for _, child := range t.Children(&tree.ShapeNode{Shape: found}) {
				children = append(children, label(child))
			}
		}
		out.Children[address] = children
		resolved, _ := t.Resolve(&tree.ShapeNode{Ref: &tree.Ref{Ref: address}})
		out.Resolve[address] = identity(resolved)
		if found := t.At(address); found != nil {
			out.Content[address] = identity(t.Content(found))
		}
	}
	return out, nil
}

func identity(shape tree.Shape) *string {
	if shape == nil {
		return nil
	}
	return shape.Base().ID
}

func refuses(envelope json.RawMessage) bool {
	var document tree.Document
	if err := json.Unmarshal(envelope, &document); err != nil {
		return true
	}
	_, err := tree.Of(&document)
	return err != nil
}

func main() {
	corpus := os.Args[1]
	raw, err := os.ReadFile(filepath.Join(corpus, "cases.json"))
	if err != nil {
		panic(err)
	}
	var cases caseFile
	if err := json.Unmarshal(raw, &cases); err != nil {
		panic(err)
	}

	out := map[string]any{}
	answered := map[string]result{}
	refuse := []bool{}
	for _, envelope := range cases.Refuse {
		refuse = append(refuse, refuses(envelope))
	}
	for name, one := range cases.Cases {
		body, err := os.ReadFile(filepath.Join(corpus, one.Tree))
		if err != nil {
			panic(err)
		}
		var document tree.Document
		if err := json.Unmarshal(body, &document); err != nil {
			panic(fmt.Errorf("%s: %w", name, err))
		}
		got, err := answers(&document, one.Expected.Probes)
		if err != nil {
			panic(fmt.Errorf("%s: %w", name, err))
		}
		answered[name] = got
	}
	out["cases"] = answered
	out["refuse"] = refuse
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(out); err != nil {
		panic(err)
	}
}

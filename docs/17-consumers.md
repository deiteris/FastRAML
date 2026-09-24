# 17. Consumers

Consumers sit on top of fastRAML: the `viewer/` SPA, the independent projects
under `contrib/`, and the shared worked document under `fixtures/`. This document
owns their boundary, shared artifacts, and gates. Consumer-specific behavior
belongs in each consumer's README.

## 1. Boundary

Consumers are downstream of the parser.

```
fastraml/  <- viewer/          through `fastraml tree` JSON
           <- raml-codegen    through `fastraml tree` JSON
           <- contrib/*       through the public parser API or tree contract
```

Nothing under `fastraml/` may import a consumer. The one packaging exception is
`fastraml serve`: it imports the optional `fastraml_viewer` package inside the
command to serve a tree document. That package provides static assets and HTTP
serving only; it does not parse or interpret RAML.

Consumers must not hold RAML language rules. A missing language rule belongs in a
parser pass. A reusable reading of an effective model belongs in `fastraml.views`.
A consumer may apply its own target-format or product policy, but must not invent
unsupported RAML syntax.

Root tests may read committed consumer artifacts to detect stale generated
output. They must not import consumer packages. `tests/unit/test_views.py` and
`tests/unit/test_bindings.py` enforce the relevant boundaries.

## 2. Shared fixture

`fixtures/sample/api.raml` is the repository's worked API. Its neighboring and
shared files exercise cross-file loading:

```
fixtures/sample/     api.raml, common.raml, invoice.json
fixtures/shared/     machine.raml, measures.raml, money.schema.json
```

`shared/` is outside `sample/`, so commands that parse the sample need a
workspace root covering `fixtures/`:

```bash
fastraml tree -w fixtures fixtures/sample/api.raml
```

The fixture is shared by the viewer, consumer suites, binding checks, and
committed tree inputs. Treat a fixture change as a cross-project contract change.
Regenerate and inspect every affected artifact and golden in the same change.

## 3. Viewer

`viewer/` is a React SPA that reads `fastraml tree` output. It is not part of
the Python package gate and decides no RAML rule. See `viewer/README.md` for UI
behavior and local development.

The viewer's checked-in contract artifacts are generated:

- `viewer/src/tree.d.ts`
- `viewer/src/walk.ts`
- `viewer/public/api.json`

Regenerate the first two from the repository root:

```bash
python -m fastraml.views.bindings typescript \
  -o viewer/src/tree.d.ts --runtime viewer/src/walk.ts
```

Regenerate the sample from `viewer/`:

```bash
npm run sample
```

Do not edit generated artifacts by hand. `tests/unit/test_bindings.py` checks
that they match generation and the shared fixture.

Viewer checks are defined by `viewer/package.json`:

```bash
npm run check  # TypeScript, layer checks, static smoke rendering
npm run ci     # check plus production build
npm run shots  # browser screenshots for local visual inspection
```

CI runs `npm run ci`; screenshots are not a CI gate.

## 4. Contrib projects

`contrib/` contains seven independently versioned `uv` projects. Each has its
own lock, dependencies, and test gate. The root `pyproject.toml` does not package
them; CI runs their gates as a matrix.

See `contrib/README.md` for the current project inventory and each project's
README for its supported behavior. In particular:

- `raml-document` is an authoring model, separate from fastRAML's parsed model.
- `raml-codegen` reads the tree contract and deliberately depends on no parser.
  Its optional `raml` extra installs `fastraml` so the command line can accept a
  `.raml` path; it runs the `tree` projection in-process and generates from the
  resulting JSON, so the generator still reads only the contract.
- `fastraml-viewer` packages the built SPA and server helper without depending on
  `fastraml`.

For most projects, work locally from the project directory:

```bash
uv sync --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy <package_name>
uv run pytest -q
```

Use the package's own `pyproject.toml` for the exact package name and optional
dependencies. Projects that package the viewer require `viewer/dist`; build the
viewer before their dependency sync when their README or CI job requires it.

### 4.1 Tree consumers

The viewer and `raml-codegen` consume tree JSON rather than parser objects. The
Python codegen bindings and runtime are generated artifacts:

- `contrib/raml-codegen/raml_codegen/tree.py`
- `contrib/raml-codegen/raml_codegen/walk.py`

Regenerate them from the repository root:

```bash
python -m fastraml.views.bindings python \
  -o contrib/raml-codegen/raml_codegen/tree.py \
  --runtime contrib/raml-codegen/raml_codegen/walk.py
```

The root binding suite checks these files and the codegen committed tree inputs.
The generated module and runtime are contract artifacts, not codegen-owned
source.

## 5. CI and publishing

`.github/workflows/ci.yml` runs:

- the root Python checks and tests
- optional-extra, bindings, benchmark-linearity, and TCK jobs
- the seven-project contrib matrix
- the viewer production gate

The `bindings` job installs Go and Node and rejects skipped binding or
cross-language conformance checks. The `contrib` job runs each project with its
own dependencies.

There are eight independently versioned distributions: `fastraml` and the seven
projects under `contrib/`. `.github/workflows/publish.yml` selects a distribution
from its tag, reruns that distribution's gate, verifies the tag version, and
publishes with Trusted Publishing. Consult the workflow for supported tag forms.

## 6. Verification

- Boundary and view imports: `tests/unit/test_views.py`
- Generated tree contracts and fixtures: `tests/unit/test_bindings.py`
- Cross-language tree behavior: `tests/unit/test_conformance.py`
- CLI serving integration: `tests/unit/test_cli.py`
- Consumer-specific behavior: each `contrib/*/README.md` and test suite

For durable design rationale that is not part of the operating contract, see
`docs/archive/views-consumers-history.md`.

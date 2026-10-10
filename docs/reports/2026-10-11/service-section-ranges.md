# Authored section ranges

Date: 2026-10-11. Branch: `fix/parked-transfers`, measured against its parent
`7e14745`. This is the fourth isolated experiment in the
[recovery plan](../../research/2026-10-10/service-recovery-plan.md). The
section positions are recorded by the parser, so the outline reads only the
model; the alternative, reading the composed source in the outline, was not
taken.

## 1. Goal and scope

An outline section (`types:`, a method's `headers:`) spanned its entries and
selected the first, because the model kept no position for its key. So an
empty section was omitted, a `schemas:` table was named `types`, and what an
Extension restated under a master resource was placed at its first entry.

Decoders now record, in `Raml.written_sections`, each section key an entity
wrote in its own file, with its owner's ID and where its value ends. The
records are held per file; the outline indexes one file's records once.
Recorded: the root's declaration tables, `uses:`, `baseUriParameters:` and
`documentation:`; a type's `facets:`; a resource's `uriParameters:`; a
method's `headers:`, `queryParameters:` and `body:`; a `describedBy:`'s
parameter groups; a response's `headers:` and `body:`; a scheme's
`describedBy:`.

The merge keeps the master's key where an Overlay or Extension wrote the same
one, so each document's root keys and resource paths are recorded from its
own tree before the merge. A key outside its owner's span, which a template
grafted, is not recorded; nor are the sections of a method or response a
template wrote, since no outline lists them, and recording them would grow
with every application.

## 2. Correctness

Tests pin: a section spans its key and entries and selects its key; empty
`traits: {}`, `uriParameters:`, `headers:` and a response's `body:` are
listed; `schemas:` keeps its name; a trait's `queryParameters:` is not the
method's; `facets:`, `describedBy:` and its `headers:` are placed at their
keys; an Extension's `types:` and restated `/a:` are placed at its own keys,
the master's at the master's; a resource type's method and response record
nothing. Over the TCK, every outline entry still holds its selection and lies
in its parent.

## 3. Results

Each parse pays one span comparison per section key a decoder meets, and a
record per key kept. On `endpoints`, 4,000 method keys a trait grafted are
rejected and 2,000 response `body:` keys are kept.

`python -m bench ab 7e14745 --config unwrap`, 5 rounds, `endpoints` again
with 7:

| workload | time | retained |
|---|---|---|
| `endpoints` | noise (13.7 %) | 17.48 → 17.77 MB (+1.65 %, inside the noise) |
| `templates` | noise | unchanged |
| `large` | noise | +0.3 % |
| `service-navigation` | noise | +0.5 % |
| `service-session` | noise | +0.3 % |

The first implementation kept a `Position` per span and ignored template
ownership; it cost `endpoints` +7.2 % time and +3.6 % retained, and
`templates` +6.6 % and +5.1 %. Flat per-file records with the end held as two
integers, and skipping template-written owners, removed those.

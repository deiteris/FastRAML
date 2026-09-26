# Partial models

**Status: accepted as a direction.** The decisions are recorded in
`research/language-service-plan.md` (D1, D2). It examines what
`parse_lenient` returns when parsing stops, and proposes how to make that
model trustworthy for consumers such as an editor. The numbered documents are
normative; this one is not. Where a proposal is taken up, it amends
`docs/11` § 2, `docs/13` § 1 and `docs/02` § 4.

## 1. Position

**Stopping is right. The model it leaves behind is the problem.**

`parse_lenient` stops at the pass where a strict parse stops (`docs/13` § 1).
That was measured, not assumed: a draft that continued to later passes turned
one missing library used by 20 types into 41 diagnostics, because each later
pass re-derived the fault from the broken state
(`tests/unit/test_lenient.py`, `TestItStopsWhereStrictStops`). Most early
failures leave later passes nothing sound to work on.

So the pipeline keeps stopping. What needs work is the model it returns, and
what a consumer may assume about it. An editor, the Sphinx extension
(`contrib/sphinxcontrib-fastraml`) and any future LSP read that model. Today
it has no stated contract, and § 2 shows where it misleads.

## 2. What a partial model looks like today

The fixture is one API with two types, a trait, an annotation type, and one
resource with a response body. It was parsed with
`parse_lenient(..., ParseOptions(unwrap=True, validate=True, retain_source=True))`,
and one mistake was introduced per row.

| Mistake | Pass | Chains | `entry_point.types` | Unknown shapes | Endpoints | Annotations bound | `unwrapped` |
|---|---|---|---|---|---|---|---|
| none | – | 0 | Good, User | 0 | 1 | 1/1 | yes |
| `minLength: two` on one type | P2 | 1 | **empty** | 1 | 0 | 0/0 | no |
| unknown root key `foo: 1` | P2 | 1 | Good, User | 1 | **0** | 0/0 | no |
| `uses:` a missing file | P3 | 1 | Good, User | 1 | 0 | 0/0 | no |
| `is: [nosuch]` | P4 | 1 | Good, User | 2 | 1 | 0/1 | no |
| unknown key in one response | P4 | 1 | Good, User | 2 | **0** | 0/1 | no |
| `securedBy: [nope]` at the root | P5 | **3** | Good, User | 2 | 1 | 0/1 | no |
| a property typed `NoSuch` | P7 | 1 | Good, User | 1 | 1 | 0/1 | no |
| `(nosuch): x` | P8 | 1 | Good, User | 0 | 1 | 0/1 | no |
| `type: [Good, N]`, string with integer | P9 | 1 | Good, N, C, User | 0 | 1 | 1/1 | **no, but every shape is flattened** |
| `example: nope` on an integer | P10 | 1 | Good, E, User | 0 | 1 | 1/1 | yes |

Five defects follow from the table. None of them is about *where* the parse
stops.

**A1. The fragment and the registry disagree.** With one bad facet on one
type, `entry_point.types` is empty, while `Raml.fragment_types` holds `Good`
and `User`, and the bad type is in neither. `unmarshal_types` accumulates the
errors of every declaration and raises before it returns, so the fragment
never receives the dict it built. One bad declaration hides every good one in
the same file from any consumer that reads the fragment.

**A2. A child's error discards its parent.** One unknown key in one response
leaves zero endpoints. The accumulator reports each sibling's error
independently, as `docs/11` § 2 promises, but each level raises before
attaching what it built. The whole endpoint tree is lost. `docs/11` § 2 lists
"responses" as able to continue independently. That is true for *reporting*,
not for *retention*.

**A3. The model does not say how far the parse got.** "The API has no
endpoints" and "P4 never ran" both look like `endpoints == {}`. An unknown
root key stops P4, although nothing P4 reads depends on it, and a consumer
cannot tell that this happened.

**A4. Broken entities look sound.** After the P9 failure, `C` (declared as
`type: [Good, N]`) has `type == 'string'` and `_unwrapped is True`. It is
missing from `Raml.shapes` but present in `fragment_types`. `Raml.unwrapped`
is `False`, yet every shape is flattened. A consumer reading `C` sees a valid
string type.

**A5. One mistake, several diagnostics, within a single pass, and in strict
parsing too.** There are two causes:

- An API-level `securedBy: [nope]` is reported three times, once for each
  level that inherited it. `apply_security_schemes` binds every inherited
  copy separately.
- A trait whose body writes `q: NoSuch`, applied to three operations, gives
  three identical chains (`resolve shape` / `reference not found`, all at the
  trait's line). Each application produces its own shape, and P7 reports each
  one.

This is the noise the stopping rule exists to prevent, and it happens inside a
single pass, so `fastraml validate` shows it today.

**A6. Recursion is left unmarked after a P9 failure. This is a safety defect.**
`unwrap_shapes` raises before `finish_unwrap`, so recursion is never marked.
Take one failing merge anywhere in the document (`C: type: [string, N]`, where
`N` is an integer). Afterwards, `Node: {properties: {next?: Node}}` is
flattened, but its cycle closes after two levels with no `RecursiveShape`.
Every consumer that stops at recursion markers (`docs/16` § 6.1) then loops
forever or hits Python's recursion limit. `contrib/sphinxcontrib-fastraml`
calls `parse_lenient(..., unwrap=True)` and can receive exactly this model.

**Where A1 and A2 come from.** In `parser/` and `types/`, about ten functions
build a container, accumulate their children's errors, and raise before
returning:

- `types/shape.py` (`unmarshal_types`);
- `parser/fragments.py` (the declaration maps, trait and resource type
  definitions);
- `parser/source_decode.py` (response, responses, operation, endpoint);
- `parser/security.py` (definition, `describedBy`);
- `parser/source_ir.py`;
- `parser/extensions.py`.

At every such site, the *siblings* of a failed entity are lost together with
it. The failed entity itself is also absent (the bad type in § 2 is in
neither map), because `make_shape` and its siblings raise without returning
the partly built entity.

## 3. Principles

1. **Report and retain are separate decisions.** An accumulator decides what
   is reported. A decoder that knows an entity's identity (its key) still
   attaches what it built, and marks it.
2. **A broken entity is marked, never passed off as sound.** A mark is the
   only thing that separates "absent" from "incomplete" from "sound".
3. **The model states how far it got.**
4. **One mistake, one diagnostic,** at the site where it was written.
5. **Nothing reads a broken entity as if it were whole.** That includes the
   passes, the views and consumers.

## 4. Signals

The signals are sparse, so a successful parse pays nothing for them
(`docs/11` § 8).

**S1. Stage.**

```python
class Pass(IntEnum):
    P0 = 0
    ...
    P10 = 10

Raml.completed: Pass | None    # last pass that finished without error
Raml.stopped_at: Pass | None   # the pass that raised; None on success
```

This resolves A3: a consumer gates each feature on the pass it needs. The
LSP's feature-to-pass table (`research/language-server.md` § 3.1) reads
these two fields directly. It also replaces `Raml.unwrapped` as the general
answer; that field stays as the P9 flag for compatibility.

**S2. Broken entities.**

```python
Raml.broken: dict[int, RamlError]   # entity id -> why it is incomplete
def is_broken(entity) -> bool: ...
```

Keyed by ID, because IDs survive `clone` (`docs/07` § 6) and object references
do not survive P9. An entry means the entity exists and its identity (name,
`key_pos`) is valid, but its content is partial. What each pass marks:

| Pass | Marked entity | What it retains |
|---|---|---|
| P2 | a declaration with a bad facet | everything except the facet |
| P2, P4 | a response, method or resource with a bad key | everything except that key |
| P4, P5 | a `DirectiveRef` or `SecurityScheme` that resolves to nothing | `resolved` or `definition` is `None` |
| P7 | a shape whose kind could not be settled | stays an `UnknownShape` |
| P8 | an unbound `DomainExtension` | `defined_by` is `None` |
| P9 | a shape whose merge failed | the declared, *unflattened* form. It must not be flagged unwrapped |

**S3. Attribution (optional).** Each chain names the entity it is about, so a
diagnostic can be linked to an outline entry and a duplicate can be
recognised. It could be a `Trace` slot or the reverse of S2. `info` is not
used for this, because `info` holds message values (`docs/11` § 6).

**S4. Placeholders for unidentified content** are not proposed. An entity
with no readable key (a non-mapping `types:` value, for example) stays absent.
Inventing a name would make up a declaration.

## 5. Contract for a partial model

This is draft text for `docs/13` § 1. When `parse_lenient` returns an error:

1. `completed` and `stopped_at` say which passes ran. Every pass up to and
   including `completed` finished. Its invariants (`docs/02` § 4) hold for
   every entity not in `broken`.
2. In the pass at `stopped_at`, each entity either satisfies that pass's
   invariant or is in `broken`. No entity is left half-processed and
   unmarked.
3. Passes after `stopped_at` did not run. Their outputs keep their
   construction defaults:

   | Pass not run | Default a consumer sees |
   |---|---|
   | P4 | `endpoints` is empty |
   | P5 | `SecurityScheme.definition` is `None` |
   | P7 | a shape's kind may be `UnknownShape` |
   | P8 | `defined_by` is `None` |
   | P9 | shapes are not flattened |
   | P10 | values are unchecked |

4. Registries and fragments agree. Every declaration reachable from a
   fragment is in `fragment_types` and its siblings, and the reverse also
   holds. This resolves A1.
5. A consumer must not run a later pass or a view that requires later
   invariants on the model, such as `unwrap_shape`, `validate`, or a view that
   requires an unwrapped model, unless `completed` covers what it needs.
   Views that need P9 check `completed`, not `unwrapped`.
6. The error holds exactly the chains a strict parse raises, with duplicates
   of one mistake collapsed (A5).

## 6. Fixes, in order of harm

Each fix is independent and preserves the stopping rule. The order is set by
how much damage each defect does to a consumer today.

1. **Mark recursion even when P9 fails (A6).** `unwrap_shapes` runs
   `finish_unwrap` over the shapes that flattened, then raises. A shape whose
   merge failed keeps its declared form and is not flagged unwrapped (A4).
   Test: a recursive type next to an unrelated failing merge still ends in a
   `RecursiveShape`. `tests/unit/test_consumer_traversal.py` gains lenient
   models as inputs.
2. **One mistake, one chain (A5).** Collapse chains whose innermost frame
   matches on location, position, message key and `info`. The collapse needs a
   representation decision (§ 6.1). Also bind an inherited `securedBy:` copy
   once per written site. Tests: a trait applied N times, and a `securedBy`
   inherited over three levels, each give one chain in strict and in lenient
   parsing.
3. **Retain siblings (A1, A2).** At each site listed under A2, attach the
   container before raising. This is a mechanical change: the siblings of a
   failed entity survive, and the failed entity is still absent. A test for
   each row of `docs/11` § 2 asserts survival *in the model*, not only in the
   error.
4. **S1 stage.** Set it in `_run_passes`, the one place that knows the
   order.
5. **S2 broken marks and failed-entity retention**, one entity kind at a time,
   as in the table in § 4. Each kind needs its own decision on what a partly
   built entity holds. A shape whose kind is unsettled, for example, stays an
   `UnknownShape`.
6. **Contract text** in `docs/13` § 1, and a `docs/11` § 2 table that
   distinguishes *reported independently* from *retained independently*.

### 6.1 Collapsing duplicate chains

When N chains share an innermost frame, they differ only in the outer frames:
which operation the trait was applied to, or which resource inherited the
scheme. Those frames are useful, for example as the LSP's
`relatedInformation` ("applied at `/a` get, `/a` post, `/b` get").

- (a) Keep the first chain and drop the rest. This is the simplest option,
  but the application sites are lost.
- (b) Keep one chain and record the others' outermost frames on it, as a new
  `RamlError` slot, not in `info`. It pickles like the rest of the error
  (`docs/11` § 1).
- (c) Deduplicate only in renderers: `str(error)`, `to_dict`, and the LSP.
  The model keeps N chains, and every consumer has to remember to collapse
  them.

*Decided (plan D1): (b), applied where accumulators merge* (`Accumulator.result`
or `RamlError.append`). `docs/11` § 2 owns it. The TCK ratchet records
pass or fail per fixture, not chain counts, so it should not move.

## 7. Later: continuation gated by containment

After § 6, a failure falls into one of four classes:

- **contained:** nothing is dropped, for example an unknown key;
- **incomplete:** an entity is retained and marked;
- **missing:** an entity is absent;
- **structural:** a fatal failure in `_FATAL`.

The stopping rule can then be refined *per class*. A pass whose errors are
all contained or incomplete lets the next pass run. That pass skips entities
in `broken`, and does not report an error caused only by a reference to a
broken entity. A pass with a missing or structural error stops, as today.
Untagged errors count as missing, so the default is today's behaviour.

This would reach the case in § 2 where an unknown root key costs every
endpoint, without reopening the noise problem. It is justified only if
measured. Over the TCK's invalid corpus and a mutation corpus (§ 8), a class
is admitted only if continuing adds no chain whose cause is already reported.
The 41-to-1 test stays as a gate. `TestItStopsWhereStrictStops` already names
this direction ("skipping the broken *entities* inside P9 and P10, not the
passes").

## 8. Verification

- **Mutation corpus.** Take the valid TCK documents and the fixtures, and
  apply one mutation each: delete a line, corrupt a scalar, misspell a
  reference, add an unknown key, break indentation. For each result assert:
  - no exception other than a `_FATAL` one;
  - registries and fragments agree;
  - every entity in `broken` has a valid name and `key_pos`;
  - no broken shape is flagged unwrapped;
  - `completed` and `stopped_at` agree with the error's pass;
  - every view gated on `completed` runs without raising.

  This is the robustness test the contract implies, and it needs no editor.
- **Retention per boundary:** one test for each row of `docs/11` § 2.
- **Noise:** one mistake gives one chain, for inherited and applied
  constructs (securedBy, traits, resource types).
- **Safety:** every lenient model the mutation corpus produces passes the
  consumer traversal law (`docs/16` § 6.1). A walk terminates without a set of
  visited ancestors.
- **Cost:** `bench run` on a valid corpus shows no allocation delta (`docs/12`
  § 5), because the signals are empty on success.

## 9. Notes

- `TestItStopsWhereStrictStops` refers to an "After-v1 item in docs/15" that
  `docs/15` no longer lists. § 7 of this document would be that item.
- The Sphinx extension already reads a lenient model. The contract in § 5 is
  what makes its "render what parsed" behaviour defensible.

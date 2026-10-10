# Cursor-local hover source lookup

Date: 2026-10-11. Branch: `fix/parked-transfers`, from master `069b41f`;
measured against its parent `131b08e`. This is the third isolated experiment
in the [recovery plan](../../research/2026-10-10/service-recovery-plan.md).

## 1. Goal, workload and cache boundaries

The goal is to remove the whole-file source-key index from the first hover in
a file, without slowing repeated hovers or changing which token is explained.

Before: the first hover in a URI sorted every key `syntax.keys` yields and
parsed every type value for built-in tokens, checked against a split of the
file's text. Both lists lived as long as the snapshot: 15,203 keys and about
2.6 MB on the hover corpus (one 13,204-line file). Later hovers bisected them.

After: each hover reads `syntax.keys_at`, the keys enclosing the cursor, one
per mapping level, found by binary search over that mapping's entries, which
are in document order. Sequences are scanned, because an alias item keeps its
anchor's earlier position. Only the type value the cursor is in is parsed for
built-in tokens. Nothing is cached; the composed tree is still the snapshot's
or the shared source owner's (docs/21 § 4), so the cost of composition is
unchanged. Include-context discovery for headerless files is unchanged.

A token's offset is taken as its column where the scalar's one-line span is
its text, or its text in quotes, rather than by comparing a line of the source.
An escape or a tag lengthens the span, so those tokens stay unexplained, as
before. A type expression folded over several lines now gets no token help;
before, a token on its first line could.

## 2. Correctness and reach

Over every TCK `.raml` file, `keys_at` at each key's start returns that key
with the same site and table as the whole-file walk (more than 10,000 keys).
A quoted union explains `nil` at its exact span; an escaped `n\x69l` explains
nothing. The existing hover suite, including unapplied-template built-ins,
opaque data, expanded aliases and inherited include contexts, passes
unchanged. The inlay reach test now fails if `Hover._keys_at` is called.

## 3. Results

`python -m bench ab 131b08e --bench hover --bench service-session --config unwrap --rounds 5`:

| workload | time | noise | peak | retained |
|---|---|---|---|---|
| `hover` | 332.9 → 337.3 ms, noise | 16.2 % | unchanged | 26.29 → 23.71 MB (−9.8 %) |
| `service-session` | 1260.5 → 1142.8 ms (−9.3 %) | 7.4 % | unchanged | 27.50 → 24.87 MB (−9.6 %) |

`hover` runs one probe per declaration, so its time is dominated by repeated
hovers; the removed index was paid once. `service-session` hovers sparsely
after each of three edits, so it pays the index once per edit and gains.

`python -m bench linearity --bench hover`: time 0.992, peak 0.992,
retained 0.998.

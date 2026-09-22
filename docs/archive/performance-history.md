# Performance history

This is non-normative evidence retained from the completed parser implementation.
Current requirements and measurement procedure are in [12](../12-performance.md).

## Recorded baseline

The committed `bench/baselines.json` was recorded on Windows, CPython 3.12, with
libyaml. It contains all five generated workloads and six configurations. Its
fingerprint is part of its meaning; the values must not be compared with another
machine, interpreter, or YAML backend.

The recorded large parse was approximately 0.34 seconds with approximately 88 MB
peak RSS. Its linearity check was within the configured 15 percent tolerance. The
recorded validation-heavy workload used substantially more memory than the large
type workload; no general RSS limit was established from it.

## Historical comparison

An implementation-era comparison ran generated workloads against go-raml on the
same Windows machine and found fastRAML approximately five to ten times slower,
depending on workload and configuration. This established only that the Python
implementation had comparable algorithmic structure, not a portable speed claim.

## Retained decisions

Profiling supported retaining the specialized YAML composition path, lazy public
exports and uncommon dependencies, flat mapping iteration, and direct graph-node
name access. It also supported measuring traced allocations separately from wall
time. Detailed profiler percentages and microbenchmark figures are intentionally
omitted: they were tied to a previous machine and code shape.

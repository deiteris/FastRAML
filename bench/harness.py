"""Measurement primitives: wall time, allocations, peak RSS.

Three deliberate choices, each of which a simpler harness gets wrong.

**Time and allocations are measured in separate runs.** `tracemalloc` hooks every
allocation and roughly triples the wall time of an allocation-heavy parse, which
is exactly what this parser is. A single run reporting both numbers reports one
true number and one useless one.

**Wall time is the minimum of the repeats, not the mean.** Scheduling noise is
one-sided: a run can be slowed by the rest of the machine and cannot be sped up
by it, so the minimum is the best estimate of the parser's own cost and the mean
mostly measures the machine.

**Peak RSS is only meaningful in a fresh process.** `ru_maxrss` is a high-water
mark for the process, monotonic and never reset, so a second bench in the same
interpreter reports the first bench's peak whenever the first was larger. The
runner therefore spawns one process per measurement (see `__main__.py`), which
also stops a warm intern table or a filled expression cache from flattering
whichever bench happens to run second.
"""

from __future__ import annotations

import gc
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ['Measurement', 'measure', 'peak_rss_bytes']


@dataclass(frozen=True, slots=True)
class Measurement:
    """One (bench, configuration) pair, measured."""

    bench: str
    config: str
    #: Best of `repeat` runs, in seconds.
    seconds: float
    #: `tracemalloc`'s peak for a single run, in bytes.
    allocated_bytes: int
    #: Process high-water mark, in bytes. `None` where the platform has no way
    #: to report one without an optional dependency.
    max_rss_bytes: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def measure(bench: str, config: str, build: Callable[[], object], *, repeat: int = 3) -> Measurement:
    """Time `build`, then measure its allocations, then read the RSS mark."""
    seconds = _fastest(build, repeat)
    allocated = _allocated(build)
    return Measurement(
        bench=bench,
        config=config,
        seconds=seconds,
        allocated_bytes=allocated,
        max_rss_bytes=peak_rss_bytes(),
    )


def _fastest(build: Callable[[], object], repeat: int) -> float:
    best = float('inf')
    for _ in range(repeat):
        gc.collect()
        start = time.perf_counter()
        result = build()
        elapsed = time.perf_counter() - start
        del result
        best = min(best, elapsed)
    return best


def _allocated(build: Callable[[], object]) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        result = build()
        _, peak = tracemalloc.get_traced_memory()
        del result
    finally:
        tracemalloc.stop()
    return peak


def peak_rss_bytes() -> int | None:
    """The process's high-water resident set, or `None` if unobtainable."""
    try:
        import resource  # noqa: PLC0415 - POSIX only, and this is the probe
    except ImportError:
        pass
    else:
        mark = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kibibytes; the BSDs and macOS report bytes.
        return mark if sys.platform == 'darwin' else mark * 1024
    return _windows_peak_rss()


def _windows_peak_rss() -> int | None:
    if sys.platform != 'win32':
        return None
    import ctypes  # noqa: PLC0415 - Windows only
    from ctypes import wintypes  # noqa: PLC0415 - see above

    class _Counters(ctypes.Structure):
        _fields_ = (
            ('cb', wintypes.DWORD),
            ('PageFaultCount', wintypes.DWORD),
            ('PeakWorkingSetSize', ctypes.c_size_t),
            ('WorkingSetSize', ctypes.c_size_t),
            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
            ('QuotaPagedPoolUsage', ctypes.c_size_t),
            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
            ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
            ('PagefileUsage', ctypes.c_size_t),
            ('PeakPagefileUsage', ctypes.c_size_t),
        )

    # A local handle, not `ctypes.windll.kernel32`: setting `argtypes` on the
    # cached module object would edit global state. The signatures are not
    # optional — without them the HANDLE is truncated to a C int and the call
    # fails, silently, by returning 0.
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD)
    kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL

    counters = _Counters()
    counters.cb = ctypes.sizeof(_Counters)
    ok = kernel32.K32GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), ctypes.sizeof(_Counters)
    )
    return int(counters.PeakWorkingSetSize) if ok else None

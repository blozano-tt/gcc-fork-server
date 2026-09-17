# GCC fork-server results

**The mechanism worked with unmodified GNU GCC 16.1.0 plus a small plugin.** Forked children continued inside `cc1plus` without `exec()`, compiled different source contents, and produced the same assembly as fresh compiler processes.

## Environment and method

- Compiler: g++-16 (Homebrew GCC 16.1.0) 16.1.0.
- Host: macOS-26.6.2-arm64-arm-64bit-Mach-O.
- Date: 2026-09-17.
- C++17, `-O2`, fixed random seed; sequential assembly output (`-S`).
- Five shuffled rounds of 20 jobs per mode and workload: 100 samples per table cell, 900 timed compilations total.
- Compiler/filesystem paths warmed before timing. Source writes, output comparisons, and executable validation excluded from timing.
- Parent startup excluded from per-job medians and included separately below.
- PCH generated before timing; both fresh GCC and forked-child PCH acceptance checked using `-H`.

## Steady-state latency

| Workload | Fresh g++ | Fresh cc1plus | Forked cc1plus | Speedup vs g++ |
|---|---:|---:|---:|---:|
| Tiny function | 55.1 ms | 40.4 ms | 8.7 ms | 6.31× |
| Small STL function | 143.0 ms | 130.6 ms | 94.9 ms | 1.51× |
| Same STL function with PCH | 89.4 ms | 76.5 ms | 45.2 ms | 1.98× |

Values are medians of individual jobs. The direct `cc1plus` control shows that the saving extends beyond skipping the g++ driver.

## Including parent startup

Each row adds the one observed parent startup to the median 20-job fork batch. This is an amortization estimate assembled from those observations, not a separately timed cold end-to-end batch. Plugin compilation and PCH creation are excluded from both comparisons.

| Workload | Parent startup | Fresh g++: 20 jobs | Forked: startup + 20 jobs |
|---|---:|---:|---:|
| Tiny function | 268.1 ms | 1090.7 ms | 442.0 ms |
| Small STL function | 54.8 ms | 2878.1 ms | 1940.9 ms |
| Same STL function with PCH | 50.8 ms | 1787.8 ms | 966.4 ms |

## Validation

- 600 byte-for-byte assembly equality checks across the three execution modes passed.
- All 300 distinct fork-generated programs were assembled, linked and executed successfully with expected-result checks.
- Each profile reported a deliberately invalid source as a failure, then compiled valid source using the same parent. Recovered output matched fresh GCC.
- The forked PCH job actually loaded the `.gch`; it did not merely succeed through textual fallback.
- Raw samples, round order, command lines, startup times and parent/child PIDs are retained in `results-macos.json`.

## Interpretation

Keeping an initialized compiler process and forking children can materially reduce latency in this toy. The benefit remains when a PCH is used. The checkpoint here precedes source/header reading, so this is startup reuse rather than shared parsed-header state.

**These are macOS/Apple Silicon frontend timings, not a tt-metal or Linux SFPI speedup.** The experiment does not accelerate assembly, linking, or LTO, and does not measure concurrent build throughput. There is no installed Linux benchmark result: the Docker daemon was unavailable.

The prototype has fixed flags and paths, one client, and sequential jobs. A production design needs a request protocol and lifecycle/concurrency handling. The next useful validation is on a Linux CI host with actual SFPI translation units and flags.

## Source and reproduction

See `README.md`, `fork_server.cc`, and `benchmark.py`. No tt-metal source files were changed.

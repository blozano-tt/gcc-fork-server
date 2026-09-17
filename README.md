# GCC fork-server experiment

This toy uses **unmodified GNU g++ plus a small plugin**. The plugin keeps one
`cc1plus` process alive at `plugin_init()`. For each request it forks; the child
returns from the plugin and proceeds through ordinary GCC compilation. There is
**no exec in the child**. The parent waits for the child and remains at the same
checkpoint for the next request.

```text
one g++ driver
  └─ one waiting cc1plus (inside plugin_init)
       ├─ fork → read source A → compile to assembly → exit
       ├─ fork → read source B → compile to assembly → exit
       └─ fork → read source C → compile to assembly → exit
```

Inspired by Neil Sexton's suggestion to fork an initialized compiler, this
demonstrates the mechanism without patching the GCC executable.
It is an experimental use of the plugin interface, not a supported GCC server mode.

On the measured macOS host, the STL-PCH workload took **89.4 ms per job with fresh
g++ versus 45.2 ms with forked children**. See [results and limitations](RESULTS.md).

## Files

- [fork_server.cc](fork_server.cc): the compiler plugin and single-client request loop.
- [benchmark.py](benchmark.py): build, timing, output comparison, and execution validation.
- [results-macos.json](results-macos.json): measured samples, commands, PIDs, startup times and checks.
- [RESULTS.md](RESULTS.md): readable measured results and limitations.

## Reproduce

Requires real GNU GCC with matching plugin development headers, Python 3, and POSIX
`fork`. On Linux, the plugin headers may require a separate GCC plugin development
package. The script adds Homebrew's include directory on Apple Silicon macOS.

```sh
python3 benchmark.py \
  --gxx /opt/homebrew/opt/gcc/bin/g++-16 \
  --work-dir /tmp/gcc-fork-toy \
  --results results-macos.json \
  --jobs 20 --rounds 5
```

On Linux pass the path to GNU `g++`, for example `/usr/bin/g++`. On macOS,
`/usr/bin/g++` is normally Apple Clang and is not suitable for this experiment.

## What is compared

1. **Fresh g++**: run the normal compiler driver for every job.
2. **Fresh cc1plus**: run the frontend command emitted by `g++ -###` directly.
3. **Forked cc1plus**: send a one-byte request to the waiting compiler and wait for
   the forked child to finish.

Each mode uses the same compiler options, input path, output path and source
contents for each job. Source contents change from job to job. Three workloads
are used: a tiny function, a small STL function, and that STL function with its
headers precompiled. All use C++17 and `-O2`.

Timings cover the client request through completion of assembly output (`-S`).
Source-file writes, assembly reads/comparisons, assembling/linking/execution checks,
PCH creation, and the initial server startup are outside individual job timings.
The results separately record server startup and a 20-job batch including startup.
Execution paths are warmed before measurement; mode order is shuffled in each of
five rounds with a fixed seed. Raw observations and round order are retained.

This is a sequential latency experiment, not a parallel build throughput test.
Fresh modes include Python's subprocess launch/wait overhead; the fork mode includes
pipe round-trip overhead. They represent the respective client interfaces rather
than a pure kernel-level fork-versus-exec microbenchmark.

## Correctness checks

- Every measured assembly file is nonempty and byte-identical across all three
  modes for the same input.
- Every distinct fork-generated program is assembled, linked, and executed with an
  expected-result check, outside timing.
- An invalid source must report failure, followed by a successful valid compilation
  using the same parent.
- GCC's `-H` output verifies that the STL PCH is actually accepted; a successful
  compilation alone would not distinguish acceptance from textual fallback.
- The results retain the parent and child PIDs.

## Deliberate limits

- The checkpoint saves executable loading, driver work and initialization before
  plugin initialization. It does **not** keep parsed STL headers or a loaded PCH in
  the parent. Each child still parses or loads them and runs subsequent compiler
  initialization and optimization.
- One parent has fixed command-line options, working directory, source filename,
  and output filename. The client rewrites the input between sequential requests.
  This is enough to test distinct inputs but is not a general compilation protocol.
- Standard input/output carry the toy protocol; source input cannot be stdin.
- The toy uses one plugin and has not audited interactions with arbitrary other
  plugins, threads, diagnostics consumers, sanitizers, or GCC versions.
- Assembly and linking are performed separately for validation and are **not**
  accelerated by this server. LTO is not used in these measurements.
- The measured host is macOS/Apple Silicon. These numbers are not estimates of
  Linux SFPI performance or full tt-metal JIT speedup.

The next useful experiment is the same benchmark on a representative Linux host,
followed by real SFPI translation units and flags. A production fork server would
need request-specific input/output handling, bounded concurrency, lifecycle/error
handling, and an explicit compiler/toolchain identity.

## References

- [GCC plugin API](https://gcc.gnu.org/onlinedocs/gccint/Plugin-API.html)
- [Historical GCC incremental compiler proposal](https://gcc.gnu.org/legacy-ml/gcc/2007-07/msg00496.html)

Recorded command paths use `<EXPERIMENT_DIR>` and `<SOURCE_DIR>` placeholders for
the original local workspace. Timings and other measurements are unchanged.

The source files in this toy are provided under the [MIT license](LICENSE).

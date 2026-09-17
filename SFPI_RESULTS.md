# SFPI fork-server results

**The same fork-server plugin works with SFPI 7.74.0 / GCC 15.1.0.** With the STL PCH, median compile-to-assembly latency fell by about 2.3× on both tested targets.

## Toolchain and scope

- Host: macOS/Apple Silicon, as in the original GNU GCC experiment.
- Cross-compiler: `riscv-tt-elf-g++ (tenstorrent/sfpi-DIY:7.74.0) 15.1.0`.
- Targets: `-mcpu=tt-bh` (Blackhole) and `-mcpu=tt-wh` (Wormhole).
- Same tiny/STL/STL-PCH sources as the original benchmark, with C++17, `-O2`, and a fixed random seed.
- Five shuffled rounds of 20 jobs per mode and workload: 100 observations per table cell; 1,800 timed compilations total.
- All three modes use the same plugin-enabled SFPI frontend. The plugin is loaded only for fork-server mode.
- Individual job timing excludes source staging, parent startup, PCH generation and object validation.
- The GCC rebuild completed before the measured runs. Native compatibility smoke-test timings during the build were not used.

## Median per-job latency

| Target | Workload | Fresh g++ | Fresh cc1plus | Forked cc1plus | Speedup vs g++ |
|---|---|---:|---:|---:|---:|
| tt-bh | Tiny function | 74.2 ms | 52.3 ms | 13.4 ms | 5.53× |
| tt-bh | STL function | 144.9 ms | 122.7 ms | 84.8 ms | 1.71× |
| tt-bh | STL function + PCH | 100.5 ms | 81.4 ms | 44.4 ms | 2.26× |
| tt-wh | Tiny function | 73.8 ms | 52.4 ms | 13.3 ms | 5.54× |
| tt-wh | STL function | 144.2 ms | 122.6 ms | 84.2 ms | 1.71× |
| tt-wh | STL function + PCH | 102.1 ms | 80.8 ms | 43.3 ms | 2.36× |

The direct `cc1plus` control confirms that savings extend beyond omitting the driver. The parent waits at plugin initialization, before source and PCH reading; children still perform that work independently.

## Parent startup and amortization

| Target | Workload | Observed parent startup | Fresh g++: 20 jobs | Forked: startup + 20 jobs |
|---|---|---:|---:|---:|
| tt-bh | Tiny function | 289.8 ms | 1489.7 ms | 555.8 ms |
| tt-bh | STL function | 60.1 ms | 2981.9 ms | 1763.8 ms |
| tt-bh | STL function + PCH | 60.4 ms | 2035.1 ms | 990.8 ms |
| tt-wh | Tiny function | 309.1 ms | 1460.9 ms | 576.3 ms |
| tt-wh | STL function | 52.4 ms | 2899.2 ms | 1784.3 ms |
| tt-wh | STL function + PCH | 64.8 ms | 2072.2 ms | 938.2 ms |

The last column adds one observed server startup to the median timed 20-job fork batch. It is an amortization estimate, not a separately timed cold batch. Compiler/plugin builds and PCH generation are excluded.

## Validation

- **1,200 assembly comparisons passed:** output was byte-identical across fresh driver, direct frontend, and forked frontend for matching input.
- **600 RISC-V object comparisons passed:** each fork-generated assembly file was assembled, then compared byte-for-byte with an object freshly compiled from that source.
- `file` identified the sampled objects as ELF 32-bit RISC-V relocatable objects.
- Both targets accepted the PCH in fresh and forked modes, verified using `-H` and `-Werror=invalid-pch`.
- All six target/workload combinations recovered after an invalid source; subsequent assembly matched fresh GCC.
- A representative final input for each of the six combinations also produced identical assembly with the original plugin-disabled SFPI installation.
- The native execution mode and new object-validation mode passed separate compatibility smoke tests after the harness changes.
- **Target programs were not executed.** No TT device or RISC-V emulator was used.

## Isolated SFPI build

The working SFPI toolchain was configured with `--disable-plugin`. To preserve it, the existing GCC stage-two build directory was copied, its GCC subdirectory was reconfigured with the captured original arguments except `--enable-plugin`, and only `cc1plus` was rebuilt with:

```sh
make -j6 CXXFLAGS=-O2 cc1plus
```

The copied generated `configargs.h` was updated to describe plugin support accurately. The existing installed g++ driver was invoked with `-B<isolated-build>/gcc/` to select the rebuilt frontend. Nothing was installed over the original SFPI toolchain.

GCC source commit: [`b1a769c65bede2f5decbc28ae0720b72b9477100`](https://github.com/tenstorrent/sfpi-gcc/commit/b1a769c65bede2f5decbc28ae0720b72b9477100). The existing working tree had a Darwin `basename` compatibility adjustment in a code generator and test-file edits. These were left as they were; no compiler source changes were introduced for this experiment. Build provenance is included in each result JSON.

The plugin itself was compiled by native Homebrew GCC 16.1.0 against the matching SFPI-generated and source headers. A cross-compiler cannot build a host-loadable plugin for its own host merely by using its default target.

## Reproduction with an uninstalled build

```sh
python3 benchmark.py \
  --gxx /path/to/sfpi/compiler/bin/riscv-tt-elf-g++ \
  --plugin-cxx /path/to/native/g++ \
  --plugin-include /path/to/plugin-enabled-build/gcc \
  --plugin-include /path/to/sfpi/gcc/gcc \
  --plugin-include /path/to/sfpi/gcc/include \
  --plugin-include /path/to/sfpi/gcc/libcpp/include \
  --extra-flag=-B/path/to/plugin-enabled-build/gcc/ \
  --extra-flag=-mcpu=tt-bh \
  --validation objects \
  --work-dir /tmp/sfpi-fork-bh \
  --results results-sfpi-bh.json \
  --jobs 20 --rounds 5
```

Change `tt-bh` to `tt-wh` and use a separate work directory for the Wormhole run. See the README for an installed compiler with plugin headers.

## Limits

This establishes the mechanism with the actual SFPI frontend, but remains a toy compile-to-assembly benchmark on macOS. It does not measure Linux host behavior, real tt-metal kernel compilation, Tensix-specific source, LTO, linking, parallel throughput, or device correctness. It uses fixed flags/paths and sequential requests. About 2.3× here is not an estimate of end-to-end tt-metal JIT acceleration.

## Raw results

- [Blackhole samples and commands](results-sfpi-bh-macos.json)
- [Wormhole samples and commands](results-sfpi-wh-macos.json)
- [Original native GNU GCC results](RESULTS.md)

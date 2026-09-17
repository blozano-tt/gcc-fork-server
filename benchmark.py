#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compare fresh g++, fresh cc1plus, and a real compiler fork server.

All timed jobs emit assembly (-S). Assembly/link/execution validation is untimed.
Run on a trusted local machine; the experimental server accepts one client.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import select
import shlex
import statistics
import subprocess
import time


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60, **kwargs)


class ForkServer:
    def __init__(self, cmd, log):
        self.log = log.open('w')
        started = time.perf_counter()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.log, bufsize=0)
        ready = self.line().split()
        assert ready[0] == 'READY', ready
        self.parent_pid = int(ready[1])
        self.startup_ms = (time.perf_counter() - started) * 1000
        self.children = []

    def line(self):
        result = bytearray()
        deadline = time.monotonic() + 60
        while not result.endswith(b'\n'):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                raise TimeoutError('Compiler server timed out; inspect its stderr log')
            char = os.read(self.proc.stdout.fileno(), 1)
            if not char:
                raise RuntimeError('Compiler server exited; inspect its stderr log')
            result.extend(char)
        return result.decode().strip()

    def compile(self):
        self.proc.stdin.write(b'C')
        response = self.line().split()
        assert response[0] == 'DONE', response
        self.children.append(int(response[1]))
        return int(response[2])

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.stdin.write(b'Q')
            assert self.proc.wait(timeout=10) == 0
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()
            self.log.close()


HEADERS = '''#pragma once
#include <algorithm>
#include <array>
#include <numeric>
#include <tuple>
#include <type_traits>
'''


def source(profile, value):
    if profile == 'tiny':
        body = f'return 3 * x + {value};'
    else:
        body = f'''std::array<int, 4> a{{x, {value}, 3, 1}};
        std::sort(a.begin(), a.end());
        return std::accumulate(a.begin(), a.end(), 0);'''
    return f'extern "C" int evaluate(int x) {{ {body} }}\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gxx', required=True, help='Real GNU g++, built with plugin support')
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=20)
    parser.add_argument('--rounds', type=int, default=5)
    args = parser.parse_args()
    if args.jobs < 1 or args.rounds < 1:
        parser.error('jobs and rounds must be positive')
    gxx = str(Path(args.gxx).resolve())
    version = run([gxx, '--version']).stdout.splitlines()[0]
    if 'clang' in version.lower():
        parser.error('This experiment requires GNU GCC, not Apple Clang')
    root = args.work_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    plugin = root / 'fork_server.so'
    plugin_inc = Path(run([gxx, '-print-file-name=plugin']).stdout.strip()) / 'include'
    build = [gxx, '-std=c++17', '-O2', '-shared', '-fPIC', '-fno-rtti', '-I' + str(plugin_inc)]
    if platform.system() == 'Darwin':
        build += ['-Wl,-undefined,dynamic_lookup']
        if Path('/opt/homebrew/include').exists():
            build += ['-I/opt/homebrew/include']
    build += [str(Path(__file__).with_name('fork_server.cc')), '-o', str(plugin)]
    run(build)
    report = dict(compiler=version, compiler_path=gxx, platform=platform.platform(),
                  jobs_per_round=args.jobs, rounds=args.rounds, timed_output='assembly (-S)',
                  plugin_build_command=build, profiles={})
    rng = random.Random(1729)
    modes = ['fresh_g++', 'fresh_cc1plus', 'forked_cc1plus']
    for profile in ['tiny', 'stl', 'stl_pch']:
        work = root / profile
        work.mkdir(exist_ok=True)
        src, asm = work / 'input.cc', work / 'output.s'
        hdr = work / 'headers.h'
        hdr.write_text(HEADERS)
        flags = ['-std=c++17', '-O2', '-frandom-seed=gcc-fork-toy']
        if profile == 'stl_pch':
            run([gxx, *flags, '-x', 'c++-header', str(hdr), '-o', str(hdr) + '.gch'])
        if profile != 'tiny':
            flags += ['-include', str(hdr)]
        src.write_text(source(profile, 0))
        cmd = [gxx, *flags, '-S', str(src), '-o', str(asm)]
        plan = run([*cmd, '-###']).stderr
        commands = [shlex.split(line) for line in plan.splitlines() if line.startswith(' ')]
        cc1 = next(command for command in commands if command and Path(command[0]).name == 'cc1plus')
        server = ForkServer([*cmd, '-fplugin=' + str(plugin)], work / 'server.log')
        item = dict(server_parent_pid=server.parent_pid, server_startup_ms=server.startup_ms,
                    gxx_command=cmd, cc1plus_command=cc1, block_order=[], timings_ms={m: [] for m in modes})
        report['profiles'][profile] = item
        expected_hashes = {}
        saved = {}
        verified = 0
        try:
            # Warm filesystem/compiler pages and each execution path before measurement.
            for mode in modes:
                if mode == 'forked_cc1plus':
                    assert server.compile() == 0
                else:
                    run(cmd if mode == 'fresh_g++' else cc1)
            if profile == 'stl_pch':
                probe = run([*cmd, '-H', '-Werror=invalid-pch'])
                accepted = any(line.startswith('! ') and str(hdr) + '.gch' in line
                               for line in probe.stderr.splitlines())
                assert accepted, probe.stderr
                item['pch_acceptance_verified'] = True
            for round_no in range(args.rounds):
                order = modes.copy()
                rng.shuffle(order)
                item['block_order'].append(order)
                for mode in order:
                    block = []
                    for job in range(args.jobs):
                        value = round_no * args.jobs + job
                        # File staging and validation are outside the timed interval.
                        src.write_text(source(profile, value))
                        asm.unlink(missing_ok=True)
                        start = time.perf_counter_ns()
                        if mode == 'forked_cc1plus':
                            assert server.compile() == 0
                        else:
                            run(cmd if mode == 'fresh_g++' else cc1)
                        block.append((time.perf_counter_ns() - start) / 1e6)
                        data = asm.read_bytes()
                        assert data
                        digest = hashlib.sha256(data).hexdigest()
                        key = (round_no, job)
                        if key in expected_hashes:
                            assert digest == expected_hashes[key], (profile, mode, key, 'assembly mismatch')
                            verified += 1
                        else:
                            expected_hashes[key] = digest
                        # Every distinct source is also assembled, linked, and executed below.
                        if mode == 'forked_cc1plus':
                            saved_path = work / f'job_{value}.s'
                            saved_path.write_bytes(data)
                            saved[value] = saved_path
                    item['timings_ms'][mode].append(block)
            # An invalid request must not poison the parent, and must not leave a stale output.
            src.write_text('this is deliberately invalid C++\n')
            asm.unlink(missing_ok=True)
            assert server.compile() != 0
            src.write_text(source(profile, 123))
            asm.unlink(missing_ok=True)
            assert server.compile() == 0 and asm.stat().st_size > 0
            item['recovered_after_invalid_source'] = True
            recovered = asm.read_bytes()
            run(cmd)
            assert recovered == asm.read_bytes()
            item['recovery_assembly_matches_fresh'] = True
        finally:
            server.close()
        if profile == 'stl_pch':
            # A successful compile alone does not establish that GCC loaded the PCH.
            probe_log = work / 'fork-pch-acceptance.log'
            probe_server = ForkServer([*cmd, '-H', '-Werror=invalid-pch',
                                       '-fplugin=' + str(plugin)], probe_log)
            try:
                assert probe_server.compile() == 0
            finally:
                probe_server.close()
            accepted = any(line.startswith('! ') and str(hdr) + '.gch' in line
                           for line in probe_log.read_text().splitlines())
            assert accepted, probe_log.read_text()
            item['fork_pch_acceptance_verified'] = True
        # Validate the actual generated machine code, outside all benchmark measurements.
        main_cc = work / 'main.cc'
        exe = work / 'validate'
        for value, saved_path in saved.items():
            expected = 3 * 7 + value if profile == 'tiny' else 7 + value + 4
            main_cc.write_text(f'extern "C" int evaluate(int);\nint main() {{ return evaluate(7) != {expected}; }}\n')
            run([gxx, str(saved_path), str(main_cc), '-o', str(exe)])
            run([str(exe)])
        item['assembly_equality_checks'] = verified
        item['executed_distinct_programs'] = len(saved)
        item['child_pids'] = server.children
        item['median_job_ms'] = {m: statistics.median(sum(item['timings_ms'][m], [])) for m in modes}
        item['median_batch_ms'] = {m: statistics.median([sum(block) for block in item['timings_ms'][m]]) for m in modes}
        item['amortized_batch_ms'] = item['median_batch_ms']['forked_cc1plus'] + server.startup_ms
        item['fork_speedup_vs_gxx'] = item['median_job_ms']['fresh_g++'] / item['median_job_ms']['forked_cc1plus']
        item['fork_speedup_vs_cc1plus'] = item['median_job_ms']['fresh_cc1plus'] / item['median_job_ms']['forked_cc1plus']
        print(profile, json.dumps(item['median_job_ms']),
              f"speedup={item['fork_speedup_vs_gxx']:.2f}x", flush=True)
        args.results.parent.mkdir(parents=True, exist_ok=True)
        args.results.write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: all assembly comparisons, executable checks, and error-recovery checks', flush=True)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""EC551 PA1: simple cross-platform CLI for the SystemVerilog synthesis engine.

Only input validation, graph evaluation, process orchestration, and report writing
happen here. Boolean minimization and structural Verilog generation remain in SV.
Python standard library only.
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from symbolic_inputs import gate_type, INVERTED, load_expression
from logisim_input import load_logisim_graph
from photo_input import IMAGE_SUFFIXES, PhotoInputError, load_photo_graph, render_overlay

ROOT = Path(__file__).resolve().parent.parent
COMMANDS = {
    1: 'Canonical SOP',
    2: 'Canonical POS',
    3: 'Inverse canonical SOP',
    4: 'Inverse canonical POS',
    5: 'Minimized SOP and literal savings',
    6: 'Minimized POS and literal savings',
    7: 'Prime implicant count',
    8: 'Essential prime implicant count',
    9: 'ON-set minterm count',
    10: 'OFF-set maxterm count',
    11: 'Truth table',
    12: 'Minimization equivalence check',
}

class InputError(Exception):
    pass


def load_sop(path: Path) -> tuple[int, list[int]]:
    # One input count, followed by fixed-width ternary cubes.
    tokens = path.read_text(encoding='utf-8').split()
    if not tokens or not tokens[0].isdigit():
        raise InputError('SOP must start with input count (2, 3, or 4)')
    n = int(tokens[0])
    if not 2 <= n <= 4:
        raise InputError('This exact optimizer supports 2, 3, or 4 inputs')
    if any(not re.fullmatch('[01-]{%d}' % n, cube) for cube in tokens[1:]):
        raise InputError(f'Each SOP cube must have {n} characters, each 0, 1, or -')
    table = []
    for m in range(1 << n):
        binary = f'{m:0{n}b}'
        table.append(int(any(all(c == '-' or c == bit for c, bit in zip(cube, binary))
                             for cube in tokens[1:])))
    return n, table


def load_graph(path: Path) -> tuple[int, list[int]]:
    """Load gate graph JSON, including Unicode/IEC symbols."""
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as exc:
        raise InputError(f'Invalid JSON: {exc}') from exc
    return evaluate_graph(data)


def evaluate_graph(data: dict) -> tuple[int, list[int]]:
    """Resolve and validate graph connections, including inversion bubbles."""
    if not isinstance(data, dict):
        raise InputError('Graph root must be a JSON object')
    names = data.get('inputs')
    gates = data.get('gates')
    output = data.get('output')
    if not isinstance(names, list) or names not in [['A','B'], ['A','B','C'], ['A','B','C','D']]:
        raise InputError('Graph inputs must be ["A","B"] (optionally followed by C, D)')
    if not isinstance(gates, list) or not isinstance(output, str):
        raise InputError('Graph must include a gates array and an output node name')
    nodes = {}
    for gate in gates:
        if not isinstance(gate, dict):
            raise InputError('Each gate must be an object')
        node_id, args = gate.get('id'), gate.get('inputs')
        label = gate.get('symbol', gate.get('type'))
        if not isinstance(node_id, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', node_id):
            raise InputError('Gate id must be an identifier (letters, digits, underscores)')
        if node_id in nodes or node_id in names:
            raise InputError(f'Duplicate or reserved gate id: {node_id}')
        if not isinstance(args, list) or any(not isinstance(a,str) for a in args):
            raise InputError(f'Gate {node_id}: inputs must be a list of node names')
        try:
            kind = gate_type(label)
            if 'type' in gate and 'symbol' in gate and gate_type(gate['type']) != kind:
                raise InputError(f'Gate {node_id}: type and symbol disagree')
        except ValueError as exc:
            raise InputError(f'Gate {node_id}: {exc}') from exc
        bubble = gate.get('output_bubble', False)
        if not isinstance(bubble, bool): raise InputError(f'Gate {node_id}: output_bubble must be true or false')
        if bubble: kind = INVERTED[kind]
        expected_count = 0 if kind.startswith('CONST') else 1 if kind in ('NOT', 'BUF') else 2
        if (len(args) != expected_count if expected_count != 2 else len(args) < 2):
            raise InputError(f'Gate {node_id}: invalid number of inputs for {kind}')
        negated = gate.get('negated_inputs', [])
        if (not isinstance(negated, list) or any(type(i) is not int for i in negated)
                or any(i < 0 or i >= len(args) for i in negated) or len(set(negated)) != len(negated)):
            raise InputError(f'Gate {node_id}: negated_inputs must list distinct 0-based pin indices')
        nodes[node_id] = (kind, args, set(negated))
    if output not in names and output not in nodes:
        raise InputError(f'Unknown graph output: {output}')

    # Validate all references, including unreachable gates, and reject cycles.
    state = {}
    def visit(node: str):
        if node in names:
            return
        if node not in nodes:
            raise InputError(f'Unknown node referenced: {node}')
        if state.get(node) == 1:
            raise InputError(f'Cycle detected involving {node}')
        if state.get(node) == 2:
            return
        state[node] = 1
        for dependency in nodes[node][1]:
            visit(dependency)
        state[node] = 2
    for node in nodes:
        visit(node)

    def evaluate(assignment: dict[str,int], node: str, memo: dict[str,int]) -> int:
        if node in assignment:
            return assignment[node]
        if node in memo:
            return memo[node]
        kind, args, negated = nodes[node]
        values = [(evaluate(assignment, a, memo) ^ int(i in negated)) for i, a in enumerate(args)]
        if kind == 'AND': result = int(all(values))
        elif kind == 'NAND': result = int(not all(values))
        elif kind == 'OR': result = int(any(values))
        elif kind == 'NOR': result = int(not any(values))
        elif kind == 'NOT': result = 1 - values[0]
        elif kind == 'BUF': result = values[0]
        elif kind == 'XOR': result = sum(values) % 2
        elif kind == 'XNOR': result = 1 - sum(values) % 2
        elif kind == 'CONST0': result = 0
        elif kind == 'CONST1': result = 1
        elif kind == 'EXACTLY1': result = int(sum(values) == 1)
        elif kind == 'NOTEXACTLY1': result = int(sum(values) != 1)
        else: raise AssertionError(kind)
        memo[node] = result
        return result

    n = len(names)
    table = []
    for m in range(1 << n):
        assignment = {name: (m >> (n - 1 - i)) & 1 for i, name in enumerate(names)}
        table.append(evaluate(assignment, output, {}))
    return n, table


def write_canonical_sop(n: int, table: list[int], path: Path) -> None:
    # Explicit minterms preserve *all* graph truth-table values for the SV engine.
    cubes = [f'{m:0{n}b}' for m, value in enumerate(table) if value]
    path.write_text(str(n) + '\n' + ('\n'.join(cubes) + '\n' if cubes else ''), encoding='utf-8')


def run_process(args: list[str], cwd: Path) -> str:
    print('> ' + subprocess.list2cmdline(args), flush=True)
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding='utf-8', errors='replace')
    if result.stdout:
        print(result.stdout, end='' if result.stdout.endswith('\n') else '\n', flush=True)
    if result.returncode:
        raise RuntimeError(f'Command exited with code {result.returncode}: {args[0]}\n{result.stdout}')
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='EC551 logic synthesis + generated-netlist test (Windows/Linux).')
    parser.add_argument('--input', '-i', type=Path, help='SOP, expression, JSON graph, Logisim .circ, or confirmed PNG/JPG photo')
    parser.add_argument('--command', '-c', default='all', help='1..12 or all (default: all)')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'output', help='Parent folder for saved reports')
    parser.add_argument('--no-simulate', action='store_true', help='Skip exhaustive generated-netlist simulation')
    parser.add_argument('--list-commands', action='store_true', help='Display the twelve individually selectable analyses')
    opts = parser.parse_args(argv)
    if opts.list_commands:
        print('all  Run all commands')
        for i, name in COMMANDS.items():
            print(f'{i:2d}   {name}')
        return 0
    if opts.input is None:
        parser.error('--input is required unless --list-commands is used')
    if opts.command.lower() == 'all':
        selected = 0
    else:
        try: selected = int(opts.command)
        except ValueError: parser.error('--command must be all or an integer 1..12')
        if selected not in COMMANDS: parser.error('--command must be all or an integer 1..12')
    input_path = opts.input.resolve()
    if not input_path.is_file(): raise InputError(f'Input file not found: {input_path}')
    extension = input_path.suffix.lower()
    if extension not in ('.sop', '.json', '.expr', '.circ') and extension not in IMAGE_SUFFIXES:
        raise InputError('Inputs must end with .sop, .json, .expr, .circ, .png, .jpg, .jpeg, .webp, or .bmp')
    is_graph = extension in ('.json','.circ')
    parsed_graph = None
    if extension in IMAGE_SUFFIXES:
        try: parsed_graph = load_photo_graph(input_path)
        except PhotoInputError as exc: raise InputError(str(exc)) from exc
        n, table = evaluate_graph(parsed_graph)
    elif extension == '.json':
        n, table = load_graph(input_path)
        parsed_graph = json.loads(input_path.read_text(encoding='utf-8-sig'))
    elif extension == '.circ':
        try: parsed_graph = load_logisim_graph(input_path)
        except ValueError as exc: raise InputError(str(exc)) from exc
        n, table = evaluate_graph(parsed_graph)
    elif extension == '.expr':
        try: n, table = load_expression(input_path)
        except ValueError as exc: raise InputError(str(exc)) from exc
    else:
        n, table = load_sop(input_path)
    case_name = re.sub(r'[^a-zA-Z0-9_-]', '_', input_path.name)
    target = opts.output_dir.resolve() / case_name
    reports, verilog, wave, temporary = (target / a for a in ('reports','verilog','waves','work'))
    for folder in (reports, verilog, wave, temporary): folder.mkdir(parents=True, exist_ok=True)
    sop_path = temporary / 'converted_to_sop.sop' if extension != '.sop' else input_path
    if extension != '.sop':
        write_canonical_sop(n, table, sop_path)
    if parsed_graph is not None:
        (temporary/'parsed_graph.json').write_text(json.dumps(parsed_graph, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    if extension in IMAGE_SUFFIXES:
        try: render_overlay(input_path, parsed_graph, reports/'photo_trace.png')
        except PhotoInputError as exc: raise InputError(str(exc)) from exc
    out_v = verilog / 'minimized_logic.v'
    out_top = verilog / 'nexys_top.v'
    compiled_engine = temporary / 'engine.vvp'
    compiled_test = temporary / 'nettest.vvp'
    expected = sum(bit << index for index, bit in enumerate(table))
    # Prefer paths relative to the project root. This avoids spaces in
    # C:/Users/<account name>/... when passed through Verilog +arg strings.
    def asarg(path: Path) -> str:
        try:
            return path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return path.resolve().as_posix()
    analysis = reports / 'analysis.txt'
    simulation = reports / 'simulation.txt'
    csv_out = reports / 'simulation.csv'
    vcd_out = wave / 'simulation.vcd'
    run_process(['iverilog', '-g2012', '-s', 'logic_engine_tb', '-o', asarg(compiled_engine),
                 asarg(ROOT / 'src' / 'logic_engine_tb.sv')], ROOT)
    analysis_text = run_process(['vvp', asarg(compiled_engine), f'+INPUT={asarg(sop_path)}',
                                 f'+OUT={asarg(out_v)}', f'+TOP={asarg(out_top)}',
                                 f'+CMD={selected}'], ROOT)
    analysis.write_text(analysis_text, encoding='utf-8')
    summary = {
        'original_input': asarg(input_path), 'format': ('verified_photo' if extension in IMAGE_SUFFIXES else {'.json':'graph', '.circ':'logisim', '.expr':'expression', '.sop':'sop'}[extension]),
        'variables': n, 'truth_table_lsb_first': table,
        'truth_hex': f'{expected:x}', 'analysis_command': selected,
        'structural_verilog': asarg(out_v), 'fpga_wrapper': asarg(out_top),
        'simulated': False, 'physical_fpga_programmed': False,
        'photo_trace': (asarg(reports/'photo_trace.png') if extension in IMAGE_SUFFIXES else None),
    }
    if not opts.no_simulate:
        run_process(['iverilog', '-g2012', '-s', 'tb_generated', '-o', asarg(compiled_test),
                     asarg(out_v), asarg(out_top), asarg(ROOT / 'tests' / 'tb_generated.sv')], ROOT)
        test_text = run_process(['vvp', asarg(compiled_test), f'+VARS={n}', f'+EXPECTED={expected:x}',
                                 f'+CSV={asarg(csv_out)}', f'+WAVE={asarg(vcd_out)}'], ROOT)
        simulation.write_text(test_text, encoding='utf-8')
        summary['simulated'] = True
        summary['simulation_passed'] = 'PASS: all ' in test_text
    (target / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(f'\nSaved result folder: {target}')
    print(f'Analysis:             {analysis}')
    if not opts.no_simulate:
        print(f'Test table:           {csv_out}')
        print(f'Simulation output:    {simulation}')
        print(f'Waveform:             {vcd_out}')
    print(f'Generated FPGA RTL:   {out_v}')
    print('NOTE: No FPGA was programmed. Vivado synthesis, bitstream generation and Hardware Manager are separate steps.')
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (InputError, OSError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)

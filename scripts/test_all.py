#!/usr/bin/env python3
"""Python validation + optional real HDL simulation regression suite."""
import csv
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from run import InputError, load_sop, load_graph, evaluate_graph, COMMANDS
from symbolic_inputs import load_expression, Parser
from logisim_input import load_logisim_graph, CircuitError
from photo_input import PhotoInputError, construct_graph, load_photo_graph, render_overlay, sidecar_path

class InputTests(unittest.TestCase):
    def test_majority_sop_matches_graph(self):
        self.assertEqual(load_sop(ROOT / 'examples/majority.sop'),
                         load_graph(ROOT / 'examples/majority.graph.json'))
        self.assertEqual(load_sop(ROOT / 'examples/majority.sop')[1],
                         [0,0,0,1,0,1,1,1])

    def test_asymmetric_sop_matches_graph(self):
        self.assertEqual(load_sop(ROOT / 'examples/asymmetric.sop'),
                         load_graph(ROOT / 'examples/asymmetric.graph.json'))
        self.assertEqual(load_graph(ROOT / 'examples/asymmetric.graph.json')[1],
                         [0,0,0,0,1,1,0,0])

    def test_xor(self):
        self.assertEqual(load_sop(ROOT / 'examples/xor.sop')[1], [0,1,1,0])

    def test_constants(self):
        self.assertEqual(load_sop(ROOT / 'examples/alwaysoff.sop')[1], [0,0,0,0])
        self.assertEqual(load_sop(ROOT / 'examples/alwayson.sop')[1], [1,1,1,1])

    def test_4bit_graph(self):
        n, result = load_graph(ROOT / 'examples/four_inputs.graph.json')
        self.assertEqual(n, 4)
        self.assertEqual(result, [1-(m.bit_count()%2) for m in range(16)])

    def test_cycle_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.json'
            path.write_text(json.dumps({'inputs':['A','B'], 'gates':[
                {'id':'g1','type':'AND','inputs':['A','g2']},
                {'id':'g2','type':'NOT','inputs':['g1']}], 'output':'g1'}))
            with self.assertRaisesRegex(InputError, 'Cycle detected'): load_graph(path)

    def test_undefined_node_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.json'
            path.write_text(json.dumps({'inputs':['A','B'], 'gates':[
                {'id':'g1','type':'AND','inputs':['A','nonexistent']}], 'output':'g1'}))
            with self.assertRaisesRegex(InputError, 'Unknown node'): load_graph(path)

class SymbolAndSchematicTests(unittest.TestCase):
    def test_unicode_majority_expression(self):
        self.assertEqual(load_expression(ROOT/'examples/majority.symbols.expr'),
                         load_sop(ROOT/'examples/majority.sop'))

    def test_asymmetric_expression(self):
        self.assertEqual(load_expression(ROOT/'examples/asymmetric.symbols.expr'),
                         load_sop(ROOT/'examples/asymmetric.sop'))

    def test_standard_sop_adjacency_and_apostrophe(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'f.expr'
            p.write_text("INPUTS: 3\nF = AB' + A'C\n", encoding='utf-8')
            n, table = load_expression(p)
            self.assertEqual(n, 3)
            self.assertEqual(table, [int((a and not b) or ((not a) and c))
                                     for a in (0,1) for b in (0,1) for c in (0,1)])

    def test_ascii_words_and_constants(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'f.expr'
            p.write_text('INPUTS: 4\nF = A AND NOT B OR 0', encoding='utf-8')
            n,t = load_expression(p)
            self.assertEqual(n,4)
            self.assertEqual(t,[int(((m>>3)&1) and not ((m>>2)&1)) for m in range(16)])

    def test_inconsistent_number_of_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'bad.expr'
            p.write_text('INPUTS: 2\nF=A∧D')
            with self.assertRaisesRegex(ValueError, 'beyond INPUTS'): load_expression(p)

    def test_iec_symbols_match_words(self):
        self.assertEqual(load_graph(ROOT/'examples/majority.iec.graph.json'),
                         load_graph(ROOT/'examples/majority.graph.json'))

    def test_iec_exactly_one_is_not_odd_parity(self):
        n, table = load_graph(ROOT/'examples/exactly_one.iec.graph.json')
        self.assertEqual(n, 3)
        self.assertEqual(table, [0,1,1,0,1,0,0,0])  # 111 is zero
        self.assertNotEqual(table[-1], 1)

    def test_output_inversion_bubble(self):
        self.assertEqual(load_graph(ROOT/'examples/nand_bubble.graph.json')[1],
                         [1,1,1,0])

    def test_input_inversion_bubble(self):
        data={'inputs':['A','B'], 'gates':[{'id':'g','symbol':'&',
             'inputs':['A','B'], 'negated_inputs':[1]}], 'output':'g'}
        self.assertEqual(evaluate_graph(data)[1],[0,0,1,0])

    def test_logisim_evolution_saved_schematic(self):
        graph = load_logisim_graph(ROOT/'examples/asymmetric.logisim.circ')
        self.assertEqual(graph['inputs'], ['A','B'])
        self.assertEqual(evaluate_graph(graph)[1], [0,0,1,0])

    def test_logisim_undriven_pin_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            source = (ROOT/'examples/asymmetric.logisim.circ').read_text()
            source = source.replace('<wire from="(100,200)" to="(210,200)"/>', '')
            p = Path(d)/'broken.circ'; p.write_text(source)
            with self.assertRaisesRegex(CircuitError, 'undriven'):
                load_logisim_graph(p)

    def test_bad_gate_symbol_rejected(self):
        data={'inputs':['A','B'], 'gates':[{'id':'g','symbol':'UNRECOGNIZED',
             'inputs':['A','B']}], 'output':'g'}
        with self.assertRaisesRegex(InputError,'Unknown logic gate symbol'):
            evaluate_graph(data)

class PhotoInputTests(unittest.TestCase):
    def test_photo_overlay_graph_matches_expected_logic(self):
        image=ROOT/'examples/asymmetric_photo.png'
        graph=load_photo_graph(image)
        self.assertEqual(graph['inputs'],['A','B'])
        self.assertEqual(evaluate_graph(graph)[1],[0,0,1,0])
        with tempfile.TemporaryDirectory() as folder:
            result=Path(folder)/'trace.png'
            render_overlay(image,graph,result)
            self.assertTrue(result.is_file())
            from PIL import Image
            with Image.open(result) as picture:
                self.assertEqual(picture.size,(1100,600))

    def test_changes_to_photo_reject_old_annotation(self):
        source=ROOT/'examples/asymmetric_photo.png'
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'my_photo.jpg'
            from PIL import Image
            Image.open(source).save(image)
            graph=construct_graph(image,{'A':[100,300],'B':[100,700]},[
              {'id':'g1','type':'AND','inputs':['A','B'],'xy':[800,500]}],'g1')
            sidecar_path(image).write_text(json.dumps(graph),encoding='utf-8')
            self.assertEqual(evaluate_graph(load_photo_graph(image))[1], [0,0,0,1])
            image.write_bytes(image.read_bytes()+b'different bytes')
            with self.assertRaisesRegex(PhotoInputError,'changed'):
                load_photo_graph(image)

    def test_missing_confirmation_is_rejected(self):
        image=ROOT/'examples/asymmetric_photo.png'
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(PhotoInputError,'photo editor'):
                load_photo_graph(image,Path(folder)/'missing.graph.json')

    def test_rejects_unverified_ai_draft(self):
        image=ROOT/'examples/asymmetric_photo.png'
        with tempfile.TemporaryDirectory() as folder:
            draft=construct_graph(image,{'A':[100,300],'B':[100,700]},[
                {'id':'g1','type':'AND','inputs':['A','B'],'xy':[800,500]}],'g1',source='ai_suggestion')
            file=Path(folder)/'draft.graph.json';file.write_text(json.dumps(draft),encoding='utf8')
            with self.assertRaisesRegex(PhotoInputError,'not reviewed'):
                load_photo_graph(image,file)

    def test_optional_vision_request_is_structured_and_never_live_in_tests(self):
        from photo_vision import suggest
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self):
                proposal={'inputs':[{'id':'A','x':100,'y':200}, {'id':'B','x':100,'y':800}],
                          'gates':[{'id':'g1','type':'AND','inputs':['A','B'],'x':700,'y':500}],
                          'output':'g1','warnings':[]}
                response={'status':'completed', 'output':[{'content':[
                    {'type':'output_text','text':json.dumps(proposal)}]}]}
                return json.dumps(response).encode('utf-8')
        with patch('urllib.request.urlopen', return_value=FakeResponse()) as request:
            result=suggest(ROOT/'examples/asymmetric_photo.png',api_key='test-key')
            payload=json.loads(request.call_args[0][0].data)
            self.assertFalse(payload['store'])
            self.assertEqual(payload['text']['format']['type'],'json_schema')
            self.assertTrue(payload['input'][0]['content'][1]['image_url'].startswith('data:image/jpeg;base64,'))
            self.assertEqual(result['gates'][0]['inputs'],['A','B'])

    def test_invalid_gate_connections_rejected(self):
        image=ROOT/'examples/asymmetric_photo.png'
        with self.assertRaisesRegex(PhotoInputError,'invalid'):
            construct_graph(image,{'A':[100,100],'B':[100,400]},[
                {'id':'g1','type':'AND','inputs':['A'],'xy':[500,300]}],'g1')

class HDLTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('iverilog') and shutil.which('vvp'), 'Icarus Verilog not installed')
    def test_full_regression(self):
        cases = ['majority.sop', 'asymmetric.sop', 'xor.sop', 'alwayson.sop',
                 'alwaysoff.sop', 'four_inputs.sop', 'majority.graph.json',
                 'asymmetric.graph.json', 'four_inputs.graph.json',
                 'majority.symbols.expr', 'asymmetric.symbols.expr', 'xor.symbols.expr',
                 'majority.iec.graph.json', 'exactly_one.iec.graph.json',
                 'nand_bubble.graph.json', 'asymmetric.logisim.circ', 'asymmetric_photo.png']
        with tempfile.TemporaryDirectory() as directory:
            for case in cases:
                with self.subTest(case=case):
                    command = [sys.executable, str(ROOT / 'scripts/run.py'),
                               '--input', str(ROOT / 'examples' / case), '--output-dir', directory]
                    process = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
                    self.assertEqual(process.returncode, 0, f'{case}: {process.stdout}\n{process.stderr}')
                    target = Path(directory) / case.replace('.', '_')
                    summary = json.loads((target/'summary.json').read_text())
                    self.assertTrue(summary['simulation_passed'])
                    with (target/'reports/simulation.csv').open(newline='') as stream:
                        rows = list(csv.DictReader(stream))
                    self.assertEqual(len(rows), 2 ** summary['variables'])
                    self.assertTrue(all(row['result'] == 'PASS' for row in rows))
            # Exercise every selectable command and verify other commands are hidden.
            for requested in COMMANDS:
                with self.subTest(command=requested):
                    command = [sys.executable, str(ROOT / 'scripts/run.py'),
                               '--input', str(ROOT / 'examples/majority.sop'),
                               '--command', str(requested), '--output-dir', directory,
                               '--no-simulate']
                    process = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
                    self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                    report = (Path(directory)/'majority_sop/reports/analysis.txt').read_text()
                    self.assertIn(f'{requested}. ', report)
                    if requested != 5:
                        self.assertNotIn('5. Minimized SOP', report)
                    if requested != 7:
                        self.assertNotIn('7. Prime implicants:', report)

if __name__ == '__main__':
    unittest.main(verbosity=2)

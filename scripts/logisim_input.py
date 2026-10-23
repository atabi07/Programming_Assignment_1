"""Conservative Logisim-evolution .circ importer for simple combinational schematics.

Reads actual saved XML wires and component pin coordinates (rather than trying
image recognition). Supports basic 1-bit labeled pins, primitive logic gates,
straight wires, fan-out, 2..4 external inputs, and east/west/north/south gates.
Unknown components and undriven/multi-driven pins are errors, not silently ignored.
This is a restricted importer, NOT a general Logisim interpreter.
"""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

class CircuitError(ValueError):
    pass

COORD = re.compile(r'\((-?\d+),\s*(-?\d+)\)\Z')
GATE_NAMES = {
    'AND Gate':'AND', 'OR Gate':'OR', 'NAND Gate':'NAND',
    'NOR Gate':'NOR', 'XOR Gate':'XOR', 'XNOR Gate':'XNOR',
    'NOT Gate':'NOT', 'Buffer':'BUF',
}

def point(value: str) -> tuple[int,int]:
    match = COORD.fullmatch(value or '')
    if not match: raise CircuitError(f'Invalid Logisim position: {value!r}')
    return int(match[1]), int(match[2])

def child_attributes(comp: ET.Element) -> dict[str,str]:
    return {attr.get('name'): attr.get('val', '') for attr in comp.findall('a')}

def vector_from_facing(facing: str, axial: int, perpendicular: int) -> tuple[int,int]:
    if facing == 'east': return -axial, perpendicular
    if facing == 'west': return axial, perpendicular
    if facing == 'north': return perpendicular, axial
    if facing == 'south': return perpendicular, -axial
    raise CircuitError(f'Unsupported gate facing {facing!r}')

def offset(xy, facing: str, axial: int, perp: int = 0):
    dx,dy = vector_from_facing(facing, axial, perp)
    return (xy[0]+dx, xy[1]+dy)

def input_offsets(inputs: int, size: int) -> list[int]:
    # Matches Logisim-evolution AbstractGate.getInputOffset (Oct 2026).
    if inputs <= 3:
        if size < 40: start, distance, even_lower = -5,10,10
        elif size < 60 or inputs <= 2: start,distance,even_lower = -10,20,20
        else: start,distance,even_lower = -15,30,30
    elif inputs == 4 and size >= 60:
        start,distance,even_lower = -5,20,0
    else:
        start,distance,even_lower = -5,10,10
    positions = []
    for i in range(inputs):
        if inputs & 1:
            dy = start*(inputs-1) + distance*i
        else:
            dy = start*inputs + distance*i
            if i >= inputs//2: dy += even_lower
            if inputs == 4 and size >= 60: dy -= 10
        positions.append(dy)
    return positions

class DSU:
    def __init__(self): self.parents = {}
    def find(self, value):
        if value not in self.parents: self.parents[value] = value
        if self.parents[value] != value: self.parents[value] = self.find(self.parents[value])
        return self.parents[value]
    def union(self, a, b):
        a,b = self.find(a), self.find(b)
        if a != b: self.parents[a] = b


def on_segment(point_, segment):
    (x,y),(u,v) = segment
    px,py = point_
    return ((x==u==px and min(y,v)<=py<=max(y,v)) or
            (y==v==py and min(x,u)<=px<=max(x,u)))


def load_logisim_graph(path: Path) -> dict:
    try:
        document = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise CircuitError(f'Invalid Logisim XML: {exc}') from exc
    if document.tag != 'project': raise CircuitError('Not a Logisim <project> XML file')
    main = document.find('main')
    selected = (main.get('name') if main is not None else None)
    circuits = document.findall('circuit')
    circuit = next((c for c in circuits if c.get('name') == selected), None)
    if circuit is None and len(circuits) == 1: circuit = circuits[0]
    if circuit is None: raise CircuitError('Cannot identify a single main circuit')

    segments = [(point(w.get('from')), point(w.get('to'))) for w in circuit.findall('wire')]
    for segment in segments:
        a,b = segment
        if a[0] != b[0] and a[1] != b[1]:
            raise CircuitError('Only horizontal/vertical wires are supported')

    drivers = []   # (name, output pin coordinate)
    sinks = []     # (consumer_name, index, pin coordinate)
    nodes = []
    outputs = []
    pins = []
    for index, comp in enumerate(circuit.findall('comp')):
        loc = point(comp.get('loc'))
        name = comp.get('name')
        attrs = child_attributes(comp)
        if name == 'Pin':
            if attrs.get('width','1') != '1': raise CircuitError('Only one-bit input/output pins are supported')
            pin_name = attrs.get('label', '').strip().upper()
            is_output = attrs.get('output','false').lower() == 'true'
            if is_output:
                if pin_name not in ('Y','F','OUT','OUTPUT'):
                    raise CircuitError('Output Pin must be labeled Y (or F, OUT, OUTPUT)')
                outputs.append((pin_name, loc))
            else:
                if pin_name not in ('A','B','C','D'):
                    raise CircuitError('Input Pins must be labeled A, B, C, D')
                pins.append((pin_name, loc))
                drivers.append((pin_name,loc))
            continue
        if name not in GATE_NAMES:
            raise CircuitError(f'Unsupported Logisim component {name!r} (subcircuits/sequential logic not supported)')
        if attrs.get('width','1') != '1': raise CircuitError('Multi-bit gate buses are not supported')
        if attrs.get('out','01') != '01': raise CircuitError('Tri-state gate outputs not supported')
        kind = GATE_NAMES[name]
        facing = attrs.get('facing','east').lower()
        gate_id = f'g{index}'
        if kind in ('BUF','NOT'):
            if name == 'NOT Gate': size = int(attrs.get('size', '30'))
            else: size = int(attrs.get('size', '30'))
            gate_inputs = 1
            pin_positions = [offset(loc, facing, size)]
            inverted = []
        else:
            size = int(attrs.get('size','50'))
            gate_inputs = int(attrs.get('inputs','2'))
            if not 2 <= gate_inputs <= 8: raise CircuitError('Only 2..8 gate input pins supported')
            # Output bubbles and XOR's extra curved line affect input x-coordinate.
            axial = size + (10 if kind in ('NAND','NOR','XNOR') else 0) + (10 if kind in ('XOR','XNOR') else 0)
            pin_positions = []
            inverted = []
            for i, dy in enumerate(input_offsets(gate_inputs, size)):
                negate = attrs.get(f'negate{i}', 'false').lower() == 'true'
                if negate: inverted.append(i)
                pin_positions.append(offset(loc, facing, axial+(10 if negate else 0), dy))
            if kind in ('XOR','XNOR') and gate_inputs>2 and attrs.get('xor','1') != 'odd':
                kind = 'EXACTLY1' if kind=='XOR' else 'NOTEXACTLY1'
        nodes.append({'id':gate_id, 'type':kind, 'inputs':[], 'negated_inputs':inverted})
        drivers.append((gate_id,loc))
        for i, position in enumerate(pin_positions): sinks.append((gate_id,i,position))

    pins_by_name = dict(pins)
    if len(pins) not in (2,3,4) or len(pins_by_name) != len(pins):
        raise CircuitError('Need 2..4 distinct labeled inputs A..D')
    names = list('ABCD'[:len(pins)])
    if sorted(pins_by_name) != names:
        raise CircuitError(f'Input labels must be consecutive {names}, got {sorted(pins_by_name)}')
    if len(outputs) != 1: raise CircuitError('Need exactly one labeled output pin Y')

    dsu = DSU()
    coordinates = set()
    for a,b in segments: coordinates.update((a,b)); dsu.union(a,b)
    for _,loc in drivers: coordinates.add(loc); dsu.find(loc)
    for _,_,loc in sinks: coordinates.add(loc); dsu.find(loc)
    for _,loc in outputs: coordinates.add(loc); dsu.find(loc)
    # Split wires at meaningful gate pins, T-junctions and endpoints.
    for segment in segments:
        for p in coordinates:
            if on_segment(p, segment): dsu.union(segment[0],p)
    net_drivers = defaultdict(list)
    for source,loc in drivers: net_drivers[dsu.find(loc)].append(source)
    def resolve(loc, label):
        sources = net_drivers.get(dsu.find(loc),[])
        if not sources: raise CircuitError(f'Unconnected/undriven pin {label} at {loc}')
        if len(sources)>1: raise CircuitError(f'Multiple drivers at {label}: {sources}')
        return sources[0]
    nodes_by_id = {n['id']:n for n in nodes}
    for gate_id,i,location in sinks:
        node = nodes_by_id[gate_id]
        node['inputs'].append((i,resolve(location, f'{gate_id}.input{i}')))
    for node in nodes:
        node['inputs'] = [value for _,value in sorted(node['inputs'])]
    output_name = resolve(outputs[0][1], 'Y')
    return {'inputs':names, 'gates':nodes, 'output':output_name}

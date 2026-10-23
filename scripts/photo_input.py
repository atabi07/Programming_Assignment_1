"""Photo schematic annotations -> validated circuit graph.

A photo is *not* a netlist. This module requires separately confirmed gate and
wire annotations. Unconfirmed photographic pixels are never treated as logic.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
GATE_TYPES = {'AND','OR','NOT','NAND','NOR','XOR','XNOR','BUF'}

class PhotoInputError(ValueError):
    pass


def sidecar_path(image: Path) -> Path:
    return image.with_suffix('.graph.json')


def digest(image: Path) -> str:
    return hashlib.sha256(image.read_bytes()).hexdigest()


def _point(value):
    if not isinstance(value, list) or len(value) != 2 or any(not isinstance(x, (int, float)) or isinstance(x, bool) or not 0 <= x <= 1000 for x in value):
        raise PhotoInputError('Every annotation position must be an [x,y] pair normalized to 0..1000')
    return [round(float(x), 1) for x in value]


def construct_graph(image: Path, input_positions: dict, gate_list: list, output: str, source: str = 'manual') -> dict:
    """Build annotated graph and check its independent provenance and topology.

    input_positions: {'A': [x,y], ...}; gate_list: [{id,type,inputs,xy}, ...].
    """
    if not image.is_file():
        raise PhotoInputError(f'Photo not found: {image}')
    if image.suffix.lower() not in IMAGE_SUFFIXES:
        raise PhotoInputError('Photo must be PNG, JPG/JPEG, WEBP, or BMP')
    names = sorted(input_positions)
    if names not in (["A", "B"], ["A", "B", "C"], ["A", "B", "C", "D"]):
        raise PhotoInputError('Place at least A and B, then optionally C and D; no skipped input letters')
    locations = {name: _point(input_positions[name]) for name in names}
    gates = []
    for gate in gate_list:
        if gate['type'] not in GATE_TYPES:
            raise PhotoInputError(f"Unsupported gate: {gate['type']}")
        ident = gate['id']
        if not isinstance(ident, str):
            raise PhotoInputError('Gate id must be a string')
        locations[ident] = _point(gate['xy'])
        gates.append({'id': ident, 'type': gate['type'], 'inputs': list(gate['inputs'])})
    data = {
        'inputs': names, 'gates': gates, 'output': output,
        'photo_annotation': {
            'source_image': image.name,
            'sha256': digest(image),
            'normalized_xy': locations,
            'annotation_method': source,
            'reviewed': source == 'manual',
        },
    }
    # Keep validation aligned with the authoritative minimizer input parser.
    from run import evaluate_graph, InputError
    try:
        evaluate_graph(data)
    except (ValueError, InputError) as exc:
        raise PhotoInputError(f'Circuit incomplete or invalid: {exc}') from exc
    return data


def load_photo_graph(image: Path, annotation: Path | None = None) -> dict:
    file = annotation or sidecar_path(image)
    if not file.is_file():
        raise PhotoInputError(f'No reviewed photo netlist found: {file}\n'
              f'Open the photo editor: python scripts/photo_editor.py "{image}"\n'
              f'Click each gate/label, connect wires, select the output, and save.')
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PhotoInputError(f'Cannot read annotation JSON: {exc}') from exc
    provenance = data.get('photo_annotation', {})
    if provenance.get('sha256') != digest(image) or provenance.get('source_image') != image.name:
        raise PhotoInputError('Photo has changed since it was annotated, or the annotation belongs to another photo. Re-annotate before synthesis.')
    if provenance.get('reviewed') is not True:
        raise PhotoInputError('AI-generated draft is not reviewed. Open the image editor and confirm/save the circuit first.')
    from run import evaluate_graph
    try:
        evaluate_graph(data)
    except ValueError as exc:
        raise PhotoInputError(str(exc)) from exc
    for location in provenance.get('normalized_xy', {}).values():
        _point(location)
    return data


def render_overlay(image: Path, graph: dict, output_path: Path) -> None:
    """Produce visual traceability screenshot for the saved report."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError as exc:
        raise PhotoInputError('Photo preview requires Pillow: python -m pip install pillow') from exc
    im = ImageOps.exif_transpose(Image.open(image)).convert('RGB')
    im.thumbnail((1600, 1400))
    w, h = im.size
    draw = ImageDraw.Draw(im)
    locations = graph['photo_annotation']['normalized_xy']
    def p(name):
        x,y = locations[name]
        return (round(x*w/1000), round(y*h/1000))
    for g in graph['gates']:
        dest = p(g['id'])
        for i, source in enumerate(g['inputs']):
            start = p(source)
            draw.line([start, dest], fill='#147cdc', width=max(2, w//400))
            # Arrowhead near gate
            dx,dy=dest[0]-start[0],dest[1]-start[1]
            norm=max((dx*dx+dy*dy)**0.5, 1)
            x=dest[0]-dx/norm*14; y=dest[1]-dy/norm*14
            draw.ellipse([x-3,y-3,x+3,y+3], fill='#147cdc')
    for name in graph['inputs']:
        x,y=p(name)
        draw.ellipse((x-10,y-10,x+10,y+10),fill='#26a269',outline='white',width=2)
        draw.text((x+11,y-17),name,fill='#00582e',stroke_width=1,stroke_fill='white')
    for gate in graph['gates']:
        x,y=p(gate['id'])
        draw.ellipse((x-14,y-14,x+14,y+14),fill='#f8a61c',outline='black',width=2)
        draw.text((x+17,y-12),f"{gate['id']}:{gate['type']}",fill='black',stroke_width=2,stroke_fill='white')
    ox,oy=p(graph['output'])
    draw.ellipse((ox-20,oy-20,ox+20,oy+20),outline='#b00020',width=4)
    output_path.parent.mkdir(parents=True,exist_ok=True)
    im.save(output_path)

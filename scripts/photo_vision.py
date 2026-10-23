"""Experimental opt-in online AI first pass for circuit photographs.

Never called automatically. It sends the supplied image to OpenAI's API only
when the user explicitly clicks AI Suggest and confirms external upload.
API calls have costs. Suggestions ALWAYS require user review in the editor.
"""
from __future__ import annotations
import base64
import io
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'inputs': {'type':'array','items':{
            'type':'object','additionalProperties':False,
            'properties': {'id':{'type':'string'},'x':{'type':'number'},'y':{'type':'number'}},
            'required':['id','x','y']}},
        'gates': {'type':'array','items':{
            'type':'object','additionalProperties':False,
            'properties': {'id':{'type':'string'},'type':{'type':'string'},
                           'inputs':{'type':'array','items':{'type':'string'}},
                           'x':{'type':'number'},'y':{'type':'number'}},
            'required':['id','type','inputs','x','y']}},
        'output': {'type':'string'},
        'warnings': {'type':'array','items':{'type':'string'}},
    },
    'required':['inputs','gates','output','warnings'],
}

INSTRUCTIONS = '''Read this photographed/drawn *combinational* logic-gate circuit.
Do NOT infer a Boolean function from missing/ambiguous wires. Reconstruct
inputs A,B,C,D and gate ids g1,g2,..., with directed connections from each
wire's source to its destination. Identify ANSI/IEEE gate shapes and IEC rectangular
symbol labels, and inversion bubbles on output as NAND/NOR/XNOR/NOT.
Represent gate types as AND,OR,NOT,NAND,NOR,XOR,XNOR,BUF only.
Coordinate x/y values are in range 0..1000, with (0,0) top-left, gate centers and
input-label centers. Use source node ids A-D or earlier/later gate ids in each
inputs array. The 'output' is a node id that drives Y. If you cannot identify
any specific part confidently, report it in warnings. Do NOT guess at wire
crossings (crossings without dots are not connected), obscured shapes, or labels.
If not confident about the diagram, prefer an incomplete draft that can be
repaired by the human reviewer. Only 2-4 primary inputs are supported.
Return an editable initial draft, not a certification of correctness.'''


def suggest(image: Path, model='gpt-4.1-mini', api_key=None):
    """Return editable AI proposal (not approved for synthesis)."""
    api_key = api_key or os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise ValueError('OPENAI_API_KEY is not set. Use manual photo tracing instead or set the key to enable optional AI suggestions.')
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ValueError('Pillow is required: python -m pip install pillow') from exc
    with Image.open(image) as original:
        im=ImageOps.exif_transpose(original).convert('RGB')
        im.thumbnail((1500,1500))
        buf=io.BytesIO()
        im.save(buf,format='JPEG',quality=85)
    data_uri='data:image/jpeg;base64,'+base64.b64encode(buf.getvalue()).decode('ascii')
    payload={
       'model': model,
       'input':[{'role':'user','content':[
           {'type':'input_text','text':INSTRUCTIONS},
           {'type':'input_image','image_url':data_uri,'detail':'high'}]}],
       'text':{'format':{'type':'json_schema','name':'circuit_photo_draft','strict':True,'schema':SCHEMA}},
       'store':False,
    }
    request=urllib.request.Request('https://api.openai.com/v1/responses',
        data=json.dumps(payload).encode('utf-8'), method='POST',
        headers={'Content-Type':'application/json', 'Authorization':f'Bearer {api_key}'})
    try:
        with urllib.request.urlopen(request, timeout=100) as response:
            raw=json.load(response)
    except urllib.error.HTTPError as exc:
        detail=exc.read().decode('utf-8','replace')[:500]
        raise ValueError(f'Vision API HTTP {exc.code}: {detail}') from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f'Vision API network error: {exc}') from exc
    chunks=[]
    for item in raw.get('output',[]):
        for part in item.get('content',[]):
            if part.get('type')=='output_text': chunks.append(part.get('text',''))
    if not chunks:
        raise ValueError(f'Vision API returned no diagram data (status={raw.get("status")}).')
    try: result=json.loads(''.join(chunks))
    except json.JSONDecodeError as exc: raise ValueError('Vision API response was not valid JSON') from exc
    return result

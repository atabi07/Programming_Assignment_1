#!/usr/bin/env python3
"""Photograph -> reviewable, user-confirmed netlist (Tkinter + Pillow).

No claim of general automatic handwritten schematic recognition: optional
AI suggestions MUST be reviewed before allowing Verilog generation.
"""
from __future__ import annotations
import argparse
import copy
import json
import subprocess
import sys
import threading
from pathlib import Path

from photo_input import construct_graph, load_photo_graph, sidecar_path, PhotoInputError

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
    from PIL import Image, ImageTk, ImageOps
except ImportError as exc:
    raise SystemExit('Photo editor needs Tkinter + Pillow. Install Pillow: python -m pip install pillow') from exc

GATES=['AND','OR','NOT','NAND','NOR','XOR','XNOR','BUF']
TOOLS=['CONNECT', 'SET OUTPUT', 'MOVE', 'DELETE', 'A', 'B', 'C', 'D'] + GATES
ROOT=Path(__file__).resolve().parent.parent

class PhotoEditor:
    def __init__(self, window: tk.Tk, image_path: Path):
        self.window=window
        self.image=image_path.resolve()
        if not self.image.is_file(): raise PhotoInputError(f'File not found: {self.image}')
        self.sidecar=sidecar_path(self.image)
        self.original=ImageOps.exif_transpose(Image.open(self.image)).convert('RGB')
        self.original.thumbnail((1150,750))
        self.iw,self.ih=self.original.size
        self.tk_image=ImageTk.PhotoImage(self.original)
        self.inputs={}
        self.gates=[]
        self.output=None
        self.pending=None
        self.history=[]
        self.dirty=False
        self.tool=tk.StringVar(value='A')
        self.status=tk.StringVar(value='Select A/B and click their labels; place gates; CONNECT wires; SET OUTPUT; Save & Run.')
        self.window.title(f'EC551 photo-to-circuit — {self.image.name}')
        self.window.geometry(f'{min(self.iw+375,1480)}x{min(self.ih+210,1000)}')
        self.layout()
        self.load_existing()
        self.render()

    def layout(self):
        outer=ttk.Frame(self.window,padding=8); outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='Trace a photograph of a logic circuit — verify every gate and connection before synthesis',
                  font=('Segoe UI',11,'bold')).pack(anchor='w')
        central=ttk.Frame(outer); central.pack(fill='both',expand=True,pady=6)
        imframe=ttk.Frame(central)
        imframe.pack(side='left',fill='both',expand=True)
        self.canvas=tk.Canvas(imframe,width=self.iw,height=self.ih,scrollregion=(0,0,self.iw,self.ih),background='#ddd')
        self.canvas.pack(side='left',fill='both',expand=True)
        sy=ttk.Scrollbar(imframe,orient='vertical',command=self.canvas.yview); sy.pack(side='right',fill='y')
        self.canvas.configure(yscrollcommand=sy.set)
        self.canvas.bind('<Button-1>',self.click)
        self.canvas.bind('<Button-3>',self.context_delete)
        side=ttk.Frame(central,padding=(14,0)); side.pack(side='right',fill='y')
        ttk.Label(side,text='Choose an annotation tool',font=('Segoe UI',10,'bold')).pack(anchor='w')
        select=ttk.Combobox(side,textvariable=self.tool,values=TOOLS,state='readonly',width=24)
        select.pack(fill='x',pady=5)
        select.bind('<<ComboboxSelected>>',lambda _e:self.reset_pending())
        ttk.Label(side,text='A–D: place input labels\nGate name: click gate center\nCONNECT: source, then destination\nSET OUTPUT: click output driver\nMOVE: click node, then new position\nDELETE: click node; right-click also deletes',justify='left').pack(anchor='w',pady=8)
        ttk.Button(side,text='Undo last edit',command=self.undo).pack(fill='x',pady=3)
        ttk.Button(side,text='Check circuit',command=self.check).pack(fill='x',pady=3)
        ttk.Button(side,text='Save verified graph',command=self.save).pack(fill='x',pady=3)
        ttk.Button(side,text='Save & Run engine',command=self.run_engine).pack(fill='x',pady=3)
        ttk.Separator(side).pack(fill='x',pady=10)
        ttk.Button(side,text='AI Suggest (optional online)',command=self.ai_suggest).pack(fill='x',pady=3)
        ttk.Label(side,text='AI requires an API key, network access,\nand approval to upload the image.\nEvery suggestion must be checked.',justify='left').pack(anchor='w')
        ttk.Label(side,text='Graph status / operations:').pack(anchor='w',pady=(12,0))
        self.log=ScrolledText(side,width=37,height=17,font=('Consolas',9),wrap='word')
        self.log.pack(fill='both',expand=True)
        ttk.Label(outer,textvariable=self.status,wraplength=1100).pack(anchor='w')

    def snapshot(self):
        self.history.append((copy.deepcopy(self.inputs),copy.deepcopy(self.gates),self.output))
        self.history=self.history[-70:]
        self.dirty=True

    def undo(self):
        if self.history:
            self.inputs,self.gates,self.output=self.history.pop()
            self.reset_pending()
            self.render()
            self.status.set('Undid last edit.')

    def reset_pending(self):
        self.pending=None
        self.render()

    def scaled(self, xy): return xy[0]*self.iw/1000,xy[1]*self.ih/1000
    def normalized(self,x,y): return [round(x*1000/self.iw,1),round(y*1000/self.ih,1)]

    def all_positions(self):
        positions=dict(self.inputs)
        positions.update({g['id']:g['xy'] for g in self.gates})
        return positions

    def hit(self,x,y):
        pos=self.all_positions()
        nearest=sorted([( (self.scaled(pt)[0]-x)**2+(self.scaled(pt)[1]-y)**2, name)
                        for name,pt in pos.items()])
        if nearest and nearest[0][0] <= 26**2: return nearest[0][1]
        return None

    def click(self,e):
        x=self.canvas.canvasx(e.x);y=self.canvas.canvasy(e.y)
        if not 0<=x<=self.iw or not 0<=y<=self.ih: return
        point=self.normalized(x,y)
        mode=self.tool.get()
        target=self.hit(x,y)
        if mode in ['A','B','C','D']:
            self.snapshot();self.inputs[mode]=point
            self.status.set(f'Placed input {mode}.')
        elif mode in GATES:
            self.snapshot()
            existing={g['id'] for g in self.gates}
            num=1
            while f'g{num}' in existing:num+=1
            self.gates.append({'id':f'g{num}','type':mode,'inputs':[],'xy':point})
            self.status.set(f'Placed {mode} gate g{num}. CONNECT its wires next.')
        elif mode=='CONNECT':
            if not target:
                self.status.set('CONNECT: click the center of an input or a gate first.');return
            if self.pending is None:
                self.pending=target
                self.status.set(f'Connecting from {target}. Now click the destination gate.')
            else:
                start=self.pending;self.pending=None
                gate=next((g for g in self.gates if g['id']==target),None)
                if gate is None:
                    self.status.set('Destination must be a gate, not a primary input.');return
                if start == target or start in gate['inputs']:
                    self.status.set('Self-connection or duplicate wire not allowed.');return
                self.snapshot();gate['inputs'].append(start)
                self.status.set(f'Connected {start} -> {target}.')
        elif mode=='SET OUTPUT':
            if target:
                self.snapshot();self.output=target;self.status.set(f'Y is driven by {target}.')
            else:self.status.set('Click an input/gate output node to select Y.')
        elif mode=='MOVE':
            if self.pending is None:
                self.pending=target
                self.status.set(f'MOVE: now click new position for {target}.' if target else 'MOVE: first click a node.')
            else:
                self.snapshot()
                if self.pending in self.inputs:self.inputs[self.pending]=point
                else:
                    next(g for g in self.gates if g['id']==self.pending)['xy']=point
                self.status.set(f'Moved {self.pending}.');self.pending=None
        elif mode=='DELETE':
            self.delete(target)
        self.render()

    def context_delete(self,e):
        self.delete(self.hit(self.canvas.canvasx(e.x),self.canvas.canvasy(e.y)))
        self.render()

    def delete(self,node):
        if not node:return
        self.snapshot()
        if node in self.inputs:del self.inputs[node]
        else:self.gates=[g for g in self.gates if g['id']!=node]
        for gate in self.gates: gate['inputs']=[x for x in gate['inputs'] if x!=node]
        if self.output==node:self.output=None
        if self.pending==node:self.pending=None
        self.status.set(f'Deleted {node} and its connected wire annotations.')

    def render(self):
        c=self.canvas
        c.delete('all')
        c.create_image(0,0,image=self.tk_image,anchor='nw')
        pos=self.all_positions()
        for gate in self.gates:
            dst=pos[gate['id']]
            for i,source in enumerate(gate['inputs']):
                if source not in pos:continue
                x1,y1=self.scaled(pos[source]);x2,y2=self.scaled(dst)
                c.create_line(x1,y1,x2,y2,fill='#126bd9',width=3,arrow='last',arrowshape=(11,12,5))
        for name,point in pos.items():
            x,y=self.scaled(point)
            is_input=name in self.inputs
            fill='#31ad70' if is_input else '#ffbc45'
            c.create_oval(x-13,y-13,x+13,y+13,fill=fill,outline='#111',width=2)
            label=name if is_input else f"{name}:"+next(g['type'] for g in self.gates if g['id']==name)
            c.create_text(x+17,y-13,text=label,fill='#07142f',anchor='sw',font=('Segoe UI',10,'bold'))
            if name==self.output: c.create_oval(x-20,y-20,x+20,y+20,outline='#cf1932',width=4)
            if name==self.pending: c.create_oval(x-23,y-23,x+23,y+23,outline='#6127af',width=3)
        self.log.delete('1.0','end')
        self.log.insert('end',f'Photo: {self.image.name}\nAnnotation: {self.sidecar.name}\nInputs: {", ".join(sorted(self.inputs)) or "none"}\nOutput Y: {self.output or "not selected"}\n\n')
        for gate in self.gates:
            self.log.insert('end',f'{gate["id"]}: {gate["type"]}({", ".join(gate["inputs"])})\n')
        if self.output:self.log.insert('end',f'\nY = {self.output}\n')
        self.log.insert('end','\nOrange = gates, green = inputs\nBlue = annotated wires\nRed ring = chosen output\n')

    def build(self):
        if not self.output:raise PhotoInputError('Select the output Y using SET OUTPUT.')
        return construct_graph(self.image,self.inputs,self.gates,self.output)

    def check(self):
        try:
            graph=self.build()
            from run import evaluate_graph
            n,table=evaluate_graph(graph)
        except (PhotoInputError,ValueError) as exc:
            messagebox.showerror('Circuit incomplete',str(exc));return False
        self.status.set(f'Validated {len(self.gates)} gates, {n} inputs. Truth vector: {table}')
        messagebox.showinfo('Topology valid',f'{n} inputs, {len(self.gates)} gates.\nTruth table: {table}\n\nThis checks logical consistency, NOT whether tracing matches the photo. Inspect all arrows and bubbles before saving.')
        return True

    def save(self):
        try: graph=self.build()
        except PhotoInputError as exc:
            messagebox.showerror('Cannot save netlist',str(exc));return False
        if not messagebox.askyesno('Confirm circuit interpretation','Do the annotated gate types, wiring, polarity bubbles, and Y output match your photograph?\n\nSave this interpretation as verified?'):
            return False
        self.sidecar.write_text(json.dumps(graph,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        self.dirty=False
        self.status.set(f'Saved verified circuit: {self.sidecar}')
        return True

    def run_engine(self):
        if not self.save():return
        self.status.set('Running EC551 minimizer and netlist testbench...')
        process=[sys.executable,str(ROOT/'scripts/run.py'),'--input',str(self.image)]
        def task():
            p=subprocess.run(process,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                             encoding='utf-8',errors='replace')
            self.window.after(0,lambda:self.finish_run(p.returncode,p.stdout))
        threading.Thread(target=task,daemon=True).start()

    def finish_run(self,rc,output):
        self.log.delete('1.0','end');self.log.insert('end',output)
        self.status.set('Simulation finished successfully. See output/ reports.' if rc==0 else 'Simulation failed. See details at right.')
        if rc != 0:messagebox.showerror('Engine error',output[-1800:])

    def load_existing(self):
        if not self.sidecar.exists():return
        try: data=load_photo_graph(self.image)
        except PhotoInputError as exc:
            messagebox.showwarning('Previous annotations not loaded',str(exc));return
        self.inputs={name:data['photo_annotation']['normalized_xy'][name] for name in data['inputs']}
        self.gates=[dict(g,xy=data['photo_annotation']['normalized_xy'][g['id']]) for g in data['gates']]
        self.output=data['output']
        self.status.set('Loaded previously verified photo annotations. Inspect or edit then Save & Run.')

    def ai_suggest(self):
        if not messagebox.askyesno('Optional online AI analysis',
             'This uploads a compressed copy of the photograph to OpenAI via its API.\n'
             'It requires OPENAI_API_KEY and may incur API charges.\n'
             'Results are UNVERIFIED and must be reviewed in the editor.\n\nContinue?'):
            return
        self.status.set('Requesting unverified AI interpretation. Review all wires afterward.')
        def task():
            try:
                from photo_vision import suggest
                proposal=suggest(self.image)
                self.window.after(0,lambda:self.import_proposal(proposal))
            except Exception as exc:
                error=str(exc)
                self.window.after(0,lambda:messagebox.showerror('AI suggestion failed',error))
        threading.Thread(target=task,daemon=True).start()

    def import_proposal(self,proposal):
        self.snapshot()
        self.inputs={node['id']:[node['x'],node['y']] for node in proposal['inputs'] if node['id'] in ['A','B','C','D']}
        self.gates=[{'id':g['id'],'type':g['type'].upper(),'inputs':g['inputs'],
                     'xy':[g['x'],g['y']]} for g in proposal['gates']]
        self.output=proposal['output']
        self.pending=None
        self.render()
        warnings='\n'.join(proposal.get('warnings',[])) or 'No issues flagged, but this is NOT proof of accurate recognition.'
        self.status.set('AI draft inserted — inspect and correct manually, then confirm Save.')
        messagebox.showwarning('AI suggestion needs human verification',
            'Every gate, junction, crossing, inverter bubble and wire must be checked.\n\nWarnings:\n'+warnings[:1600])


def main():
    arg=argparse.ArgumentParser(description='Mark gate symbols and wires over PNG/JPG photo, then produce a verified graph.')
    arg.add_argument('image',nargs='?',type=Path,help='Photo or screenshot (PNG, JPEG, WEBP, BMP)')
    opts=arg.parse_args()
    root=tk.Tk()
    path=opts.image
    if path is None:
        filename=filedialog.askopenfilename(title='Select circuit photo',filetypes=[('Images','*.png *.jpg *.jpeg *.webp *.bmp')])
        if not filename:return
        path=Path(filename)
    try:app=PhotoEditor(root,path)
    except Exception as exc:
        messagebox.showerror('Cannot open photo',str(exc));root.destroy();return
    root.mainloop()

if __name__=='__main__':main()

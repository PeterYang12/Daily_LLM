#!/usr/bin/env python3
"""Render previews and validate local presentation/report artifacts; no service calls."""
from pathlib import Path
from datetime import datetime, timezone
import json, hashlib, re
import fitz
from PIL import Image
from pptx import Presentation

B=Path(__file__).resolve().parent
ROOT=B.parents[1]
pptx=B/'CPU_Sandbox_Benchmark_Data_EN.pptx'
pdf=B/'CPU_Sandbox_Benchmark_Data_EN.pdf'
prs=Presentation(pptx);doc=fitz.open(pdf)
assert len(prs.slides)==len(doc)==6
chart_count=sum(sh.has_chart for sl in prs.slides for sh in sl.shapes)
assert chart_count==4
notes=[s.notes_slide.notes_text_frame.text for s in prs.slides]
assert all('Sources' in s and 'Study scope' in s for s in notes)
shape_bounds=[]
for i,sl in enumerate(prs.slides,1):
    for sh in sl.shapes:
        if sh.left < -10 or sh.top < -10 or sh.left+sh.width>prs.slide_width+10 or sh.top+sh.height>prs.slide_height+10:
            shape_bounds.append({'slide':i,'shape':sh.name})
assert not shape_bounds,shape_bounds
text='\n'.join(p.get_text() for p in doc)
assert not re.search(r'[\u3400-\u9fff]',text)
assert '\ufffd' not in text
assert 'Series1' not in text
for value in ['1,947','2,475','1,055','3.64','5.51','5.88','5.47','0.46','0.40','0.54','2.91','1,027','18/192','1/171,318','envd startup timed out','512']:
    assert value in text,value
# Charts have correct numeric-only labels; descriptive titles should appear once.
assert doc[1].get_text().count('Successful create-delete cycles / s')==1
bounds=[]
for i,page in enumerate(doc,1):
    for word in page.get_text('words'):
        r=fitz.Rect(word[:4])
        if not page.rect.contains(r):bounds.append({'page':i,'word':word[4],'rect':list(r)})
assert not bounds,bounds
thumbs=[]
for i,page in enumerate(doc,1):
    page.get_pixmap(matrix=fitz.Matrix(1.6,1.6),alpha=False).save(B/f'slide-{i}.png')
    im=Image.open(B/f'slide-{i}.png');im.thumbnail((800,450));thumbs.append(im)
canvas=Image.new('RGB',(1600,1350),'#DCE4EC')
for i,im in enumerate(thumbs):canvas.paste(im,((i%2)*800,(i//2)*450))
canvas.save(B/'overview.png')
report=fitz.open(B/'Benchmark_Findings_EN.pdf');assert len(report)==2
for i,page in enumerate(report,1):
    assert not re.search(r'[\u3400-\u9fff]',page.get_text())
    assert all(page.rect.contains(fitz.Rect(w[:4])) for w in page.get_text('words'))
    page.get_pixmap(matrix=fitz.Matrix(1.25,1.25),alpha=False).save(B/f'report-{i}.png')
source=json.loads((B/'sources.json').read_text())
for entry in source['source_files']:
    assert hashlib.sha256((ROOT/entry['path']).read_bytes()).hexdigest()==entry['sha256']
validation={'validated_utc':datetime.now(timezone.utc).isoformat(),'status':'passed','presentation_pages':6,'report_pages':2,
 'native_editable_charts':chart_count,'all_slides_have_source_notes':True,'pptx_shapes_within_page':True,'pdf_text_within_page':True,
 'english_text_checked':True,'selected_values_checked':True,'source_hashes_checked':len(source['source_files']),
 'render_engine':'Portable LibreOffice 24.2.7.2; local Aptos to DejaVu Sans mapping',
 'scope':'Presentation/report artifact checks; no benchmark reruns and no service configuration changes.'}
(B/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
files=[p for p in sorted(B.iterdir()) if p.is_file() and p.name!='manifest.json']
manifest={'generated_utc':datetime.now(timezone.utc).isoformat(),'revision':'data_driven_en','files':[{'name':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]}
(B/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(validation,indent=2))

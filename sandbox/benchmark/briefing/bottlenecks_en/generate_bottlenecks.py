#!/usr/bin/env python3
"""English, mechanism-first sandbox briefing; no service or benchmark calls."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

OUT=Path(__file__).resolve().parent
BASE=OUT.parents[1]
FONT='Aptos'
pdfmetrics.registerFont(TTFont('DV','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
pdfmetrics.registerFont(TTFont('DV-Bold','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
pdfmetrics.registerFontFamily('DV',normal='DV',bold='DV-Bold',italic='DV',boldItalic='DV-Bold')
C={'bg':'F4F6F9','navy':'15283F','text':'30465D','muted':'5D7187','line':'DBE3EC',
   'white':'FFFFFF','blue':'2563B8','teal':'087D78','pale':'E9F0F9','mint':'E6F3EF',
   'orange':'AE5A21','sand':'FFF2E7'}
AUDIT=[]

def rgb(c):return RGBColor.from_string(C.get(c,c))

def box(s,x,y,w,h,color='white',border=None,rounded=False):
    sh=s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
                         Inches(x), Inches(y), Inches(w), Inches(h))
    sh.fill.solid();sh.fill.fore_color.rgb=rgb(color)
    if border:sh.line.color.rgb=rgb(border);sh.line.width=Pt(.8)
    else:sh.line.fill.background()
    if rounded:sh.adjustments[0]=.055
    # Remove theme-derived shadows for a flat, readable deck.
    for st in sh._element.findall('{http://schemas.openxmlformats.org/presentationml/2006/main}style'):
        sh._element.remove(st)
    sh._element.spPr.append(OxmlElement('a:effectLst'))
    return sh

def wrap(value,width,size,bold=False):
    lines=[]
    for paragraph in value.split('\n'):
        line=''
        for word in paragraph.split():
            candidate=(line+' '+word).strip()
            if line and pdfmetrics.stringWidth(candidate,'DV-Bold' if bold else 'DV',size)>width*72:
                lines.append(line);line=word
            else:line=candidate
        lines.append(line)
    return '\n'.join(lines)

def text(s,value,x,y,w,h,size=16,color='text',bold=False,align=PP_ALIGN.LEFT,wrapped=False):
    if wrapped:value=wrap(value,w,size,bold)
    lines=value.split('\n')
    widths=[pdfmetrics.stringWidth(line,'DV-Bold' if bold else 'DV',size) for line in lines]
    required=len(lines)*size*1.18
    if max(widths,default=0)>w*72+.1 or required>h*72+.8:
        raise ValueError(f'Overflow: {value!r}; {max(widths)}/{w*72} width; {required}/{h*72} height')
    AUDIT.append({'text':value,'size':size,'lines':len(lines),'width_pt':max(widths,default=0),
                  'box_width_pt':w*72,'required_height_pt':required,'box_height_pt':h*72})
    sh=s.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h))
    tf=sh.text_frame;tf.clear();tf.word_wrap=True;tf.auto_size=MSO_AUTO_SIZE.NONE
    tf.margin_left=tf.margin_right=tf.margin_top=tf.margin_bottom=0;tf.vertical_anchor=MSO_ANCHOR.TOP
    for i,line in enumerate(lines):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph()
        p.alignment=align;p.line_spacing=Pt(size*1.18);p.space_before=p.space_after=Pt(0)
        r=p.add_run();r.text=line;r.font.name=FONT;r.font.size=Pt(size);r.font.bold=bold;r.font.color.rgb=rgb(color)
        r._r.get_or_add_rPr().set('lang','en-US')
    return sh

def slide(prs,index,title,subtitle):
    s=prs.slides.add_slide(prs.slide_layouts[6]);s.background.fill.solid();s.background.fill.fore_color.rgb=rgb('bg')
    box(s,.53,.38,.07,.20,'blue')
    text(s,'SANDBOX BOTTLENECKS  /  AGENTIC RL',.73,.36,10.9,.25,10.8,'blue',True)
    text(s,title,.55,.84,12.23,.58,29,'navy',True)
    text(s,subtitle,.57,1.49,12.04,.36,15.5,'muted')
    box(s,.55,7.08,12.23,.012,'line')
    text(s,'E2B / Firecracker context · Mechanisms, examples and profiling targets',.57,7.20,11.1,.18,8.8,'muted')
    text(s,f'{index}/2',12.13,7.18,.64,.22,10,'muted',align=PP_ALIGN.RIGHT)
    return s

EXECUTION=[
 {'title':'Cold Start & Reset',
  'mechanism':'Provisioning, runtime warmup and clean-state restoration may outlast the useful task.',
  'example':'Recreate a sandbox for every one-line Python action: setup is paid every time.',
  'inspect':'Inspect: readiness, first-use and reset time.'},
 {'title':'Process Creation\n& System Calls',
  'mechanism':'Process launches, pipes and scheduling add work; virtualized I/O can add latency.',
  'example':'An agent runs hundreds of tiny shell commands; launching them costs more than the useful work.',
  'inspect':'Inspect: fork/exec, syscalls, context switches.'},
 {'title':'Filesystem I/O',
  'mechanism':'Many small files increase metadata work; uncached reads and copy-on-write updates can add latency.',
  'example':'Installing packages and scanning a repository touch thousands of small files.',
  'inspect':'Inspect: IOPS, metadata latency, cache state.'},
]
SCALING=[
 {'title':'CPU Quotas & Throttling',
  'mechanism':'With a CPU-time quota, a busy sandbox can exhaust its budget and wait until the next period.',
  'example':'A build stalls despite spare host cores because its own CPU budget is exhausted.',
  'inspect':'Check: cpu.max, throttled_usec, nr_throttled.'},
 {'title':'Memory Pressure',
  'mechanism':'Runtime state and caches grow with sandbox count. Reclaim, allocation stalls and OOM can disrupt work.',
  'example':'Parallel test workers exceed available RAM and are stalled or killed by the kernel.',
  'inspect':'Check: working set, memory PSI, memory.events.'},
 {'title':'Resource Contention',
  'mechanism':'Sandboxes share cores, caches, memory bandwidth and storage; bursts can increase queueing and tail latency.',
  'example':'Simultaneous test runs delay other commands and can push new sandboxes past readiness timeouts.',
  'inspect':'Check: p95/p99, queues, per-core CPU, I/O pressure.'},
 {'title':'Compatibility Limitations',
  'mechanism':'Restricted permissions, missing packages or kernel features can prevent a tool from working.',
  'example':'A debugger needs ptrace; Docker or nested KVM may require privileges or /dev/kvm.',
  'inspect':'Check: required syscalls, capabilities, devices, kernels.'},
]

def make_slides():
    prs=Presentation();prs.slide_width=Inches(13.333333);prs.slide_height=Inches(7.5)
    prs.core_properties.title='Sandbox Bottlenecks in Agentic RL'
    prs.core_properties.subject='Mechanisms, examples and profiling targets'
    prs.core_properties.author='Sandbox benchmark study'
    s=slide(prs,1,'Why short sandbox tasks feel slow',
            'Pure CPU computation is not always the main cost of an agent workload.')
    labels=['Reset / restore','Launch processes','Read / edit files','Useful computation','Return results']
    for i,label in enumerate(labels):
        x=.55+i*2.50
        box(s,x,2.02,2.23,.43,'mint' if i==3 else 'pale',rounded=True)
        text(s,label,x+.08,2.135,2.07,.23,11.5,'teal' if i==3 else 'blue',True,align=PP_ALIGN.CENTER)
        if i<4:text(s,'→',x+2.29,2.105,.18,.25,13,'muted')
    for i,item in enumerate(EXECUTION):
        x=.55+i*4.16;y=2.79;w=3.91;h=3.70
        box(s,x,y,w,h,'white','line',True)
        box(s,x+.22,y+.23,.04,.45,'blue')
        text(s,item['title'],x+.39,y+.19,w-.63,.69,18.5,'navy',True)
        text(s,item['mechanism'],x+.23,y+1.00,w-.46,.99,14.8,'text',wrapped=True)
        text(s,'EXAMPLE',x+.23,y+2.05,w-.46,.20,10.3,'teal',True)
        text(s,item['example'],x+.23,y+2.35,w-.46,.94,14.0,'text',wrapped=True)
        text(s,item['inspect'],x+.23,y+3.40,w-.46,.20,9.8,'muted',wrapped=True)
    text(s,'Observed in our local Code Interpreter test: first call 1,027 ms vs second 68 ms (P50; 60 pairs).',
         .59,6.69,12.10,.29,11.5,'teal',True)
    text(s,'Firecracker runs normal Linux syscalls in the guest kernel; not every syscall is intercepted by the host.',
         .59,6.96,12.07,.18,8.8,'muted')
    s.notes_slide.notes_text_frame.text=(
      'Main point: short agent actions can spend more wall time on environment setup, process launches, '
      'filesystem activity and tool transport than on useful computation. These are illustrative bottleneck mechanisms.\n'
      'Cold reset means preparing a fresh clean state. Pause/resume preserves memory and files and must not be '
      'described as a clean reset. The original experiment verified 120 pause/resume cycles, not generic clean-reset speed.\n'
      'Firecracker uses a full guest Linux kernel. Ordinary Linux syscalls execute there; they do not universally '
      'pass through a per-syscall security proxy. VM exits, virtualized device I/O, process creation, pipes and '
      'scheduling should be profiled separately.\n'
      'The labeled observation is from the local minimal Code Interpreter template: first run_code P50 1026.74 ms '
      'and second 68.39 ms across 60 pairs. It supports first-use cost, not a unique attribution to SDK, syscalls '
      'or process creation. Filesystem bottlenecks were not established by the reported compressed-file measurements.\n'
      'Sources: ../../phase2-sdk/APPLICATION-RESULTS.md; ../../phase1-service/LIFECYCLE-RESULTS.md; ../../REPORT.md.')

    s=slide(prs,2,'Scaling and platform limits',
            'Shared resources can add long tails; missing capabilities can block a task entirely.')
    for i,item in enumerate(SCALING):
        x=.55+(i%2)*6.25;y=2.03+(i//2)*2.36;w=5.98;h=2.22
        box(s,x,y,w,h,'white','line',True)
        text(s,item['title'],x+.23,y+.17,w-.46,.35,18.3,'navy',True)
        text(s,item['mechanism'],x+.23,y+.65,w-.46,.54,14.0,'text',wrapped=True)
        text(s,'EXAMPLE',x+.23,y+1.22,w-.46,.18,10.0,'teal',True)
        text(s,item['example'],x+.23,y+1.46,w-.46,.50,13.2,'text',wrapped=True)
        text(s,item['inspect'],x+.23,y+2.01,w-.46,.19,10.0,'muted',wrapped=True)
    text(s,'Observed correlation: readiness timeouts coincided with CPU bursts; the causal path remains unresolved.',
         .59,6.72,12.10,.25,11.0,'teal',True)
    text(s,'CPU throttling and OOM were not established in this run. Swap was disabled in the tested VM.',
         .59,6.96,12.05,.18,8.8,'muted')
    s.notes_slide.notes_text_frame.text=(
      'CPU throttling is conditional on an enforced CPU-time quota. A vCPU count or the 512-sandbox running quota '
      'is not itself a CPU bandwidth quota. The startup failure evidence did not establish CPU-quota throttling.\n'
      'Memory pressure can cause reclaim and allocation stalls without swap. The measured host and outer VM had '
      'no swap; memory exhaustion and OOM were not established as the observed startup failure cause.\n'
      'In the N=256 resident-pool experiment with tool concurrency 8, three of five preparation attempts failed. '
      'Two involved envd readiness timeouts and CPU bursts; one involved the Firecracker management socket. '
      'These are correlations and failure observations, not a proven single contention mechanism or a hard capacity limit.\n'
      'Compatibility examples depend on the guest kernel, template, permissions and exposed devices. They were '
      'not demonstrated as failures in this benchmark. An unsupported operation is a functional limitation, '
      'which should be separated from slow execution.\n'
      'Sources: ../../phase1-service/results/setup-failure-log-20260920.md; '
      '../../phase1-service/IDLE-POOL-RESULTS.md; ../../phase0-environment/results/inventory.json.\n'
      'Scope: local synthetic stage 0–2 tests. No measured real-RL training speedup or sustained capacity certification.')
    prs.save(OUT/'Agentic_RL_Sandbox_Bottlenecks_EN.pptx')
    (OUT/'layout-check.json').write_text(json.dumps({'slides':2,'text_blocks':AUDIT},indent=2)+'\n')

REPORT='''# Sandbox Bottlenecks in Agentic RL

**Mechanisms, concrete examples and profiling targets**

Pure CPU computation is not always the main cost. For short agent tool calls, process creation, system calls, filesystem activity and preparing a clean environment can outweigh the useful computation. The categories below are examples to investigate, not seven confirmed diagnoses from our E2B run.

| Bottleneck | Example in an agent workload | What to inspect |
|---|---|---|
| CPU throttling | A build exhausts an enforced CPU-time budget and waits for the next quota period, even with host cores idle. | cpu.max, throttled_usec, nr_throttled; distinguish CPU quotas from vCPU count. |
| Cold start and reset | Recreating a clean sandbox for every short Python action repeats readiness and runtime setup. | Create → ready → first command; dependency/cache state; fresh-state reset time. |
| System calls and process creation | Hundreds of tiny subprocesses spend time on launches, pipes, scheduling and I/O. | Process-start latency, syscall counts, context switches and relevant VM exits. |
| Filesystem I/O | Package installs, repo scans and patches touch thousands of small files. | Metadata latency, IOPS, cache misses and copy-on-write effects; control compression. |
| Memory pressure | Parallel test workers increase working sets until reclaim, allocation stalls or OOM disrupt execution. | Working set, memory PSI and memory.events; swap only if configured. |
| Resource contention | Simultaneous launches and tool calls compete for CPU, caches, memory bandwidth and storage. | p95/p99, queues, per-core CPU and I/O pressure; compare isolated and shared load. |
| Compatibility limitations | A debugger needs ptrace; Docker or nested KVM may need missing privileges or /dev/kvm. | Functional preflight for required syscalls, kernel features, packages and devices. |

## Observations from the local E2B study

- **First-use cost:** first run_code P50 **1,027 ms**, second **68 ms**, across 60 paired sandboxes. This does not isolate SDK, syscall or process-start cost.
- **Startup reliability:** the target-256 resident-pool experiment had **2 measured runs and 3 preparation failures** out of 5 attempts. Readiness timeouts coincided with CPU bursts; a unique cause and hard capacity limit remain unproven.

## Important distinctions

Firecracker executes normal Linux syscalls in its guest kernel; it does not universally intercept every syscall in a separate security layer. Pause/resume preserves state and is not a clean reset. CPU-quota throttling and OOM were not established by this study, and the tested VM had no swap. Compatibility examples are deployment-dependent and were not benchmark findings.

The measurements are from a local Firecracker deployment and a minimal Code Interpreter template. They are short synthetic tests, not measured real-RL training speedups. Before attributing a bottleneck, validate workload correctness, timing boundaries and load-generator headroom.

Sources: [Full study](../../REPORT.md), [Application results](../../phase2-sdk/APPLICATION-RESULTS.md), [Startup evidence](../../phase1-service/results/setup-failure-log-20260920.md), [Lifecycle results](../../phase1-service/LIFECYCLE-RESULTS.md).
'''

def make_report():
    styles={
       'title':ParagraphStyle('title',fontName='DV-Bold',fontSize=20,leading=26,textColor=colors.HexColor('#15283F'),spaceAfter=6),
       'sub':ParagraphStyle('sub',fontName='DV',fontSize=10.5,leading=15,textColor=colors.HexColor('#2563B8'),spaceAfter=12),
       'body':ParagraphStyle('body',fontName='DV',fontSize=9.6,leading=14,textColor=colors.HexColor('#30465D'),spaceAfter=9),
       'small':ParagraphStyle('small',fontName='DV',fontSize=8.7,leading=12.3,textColor=colors.HexColor('#5D7187'),spaceAfter=7),
       'cell':ParagraphStyle('cell',fontName='DV',fontSize=8.5,leading=12.3,textColor=colors.HexColor('#30465D')),
       'head':ParagraphStyle('head',fontName='DV-Bold',fontSize=8.8,leading=12,textColor=colors.white),
    }
    doc=SimpleDocTemplate(str(OUT/'Sandbox_Bottlenecks_Report_EN.pdf'),pagesize=A4,
        topMargin=33,bottomMargin=31,leftMargin=34,rightMargin=34,
        title='Sandbox Bottlenecks in Agentic RL',author='Sandbox benchmark study')
    story=[Paragraph('Sandbox Bottlenecks in Agentic RL',styles['title']),
           Paragraph('Mechanisms, examples and profiling targets',styles['sub']),
           Paragraph('<b>Pure CPU computation is not always the main cost.</b> For short tool calls, process creation, '
                     'system calls, filesystem activity and preparing a clean environment can outweigh useful computation. '
                     'These are examples to investigate, not seven confirmed diagnoses.',styles['body'])]
    rows=[['Bottleneck','Example in an agent workload','What to inspect'],
          ['CPU throttling','A build exhausts an enforced CPU-time budget and waits for the next period, even with host cores idle.','cpu.max; throttled_usec; nr_throttled. A vCPU count is not a CPU-time quota.'],
          ['Cold start / reset','Recreating a clean sandbox for each short Python action repeats readiness and runtime setup.','Create → ready → first command; dependencies, cache state and reset time.'],
          ['Syscalls / process creation','Hundreds of tiny subprocesses spend time launching, using pipes, scheduling and performing I/O.','Process-start latency, syscall counts, context switches and relevant VM exits.'],
          ['Filesystem I/O','Package installs, repo scans and patches touch thousands of small files.','Metadata latency, IOPS, cache misses, copy-on-write effects and compression.'],
          ['Memory pressure','Parallel test workers grow working sets; reclaim, allocation stalls or OOM disrupt execution.','Working set, memory PSI, memory.events. Swap only if configured.'],
          ['Resource contention','Simultaneous launches and tool calls compete for cores, caches, memory bandwidth and storage.','p95/p99, queueing, per-core CPU and I/O pressure; isolated vs shared load.'],
          ['Compatibility limits','A debugger needs ptrace; Docker or nested KVM may need missing privileges or /dev/kvm.','Functional preflight for required syscalls, kernel features, packages and devices.']]
    cells=[[Paragraph(v,styles['head'] if i==0 else styles['cell'])for v in row]for i,row in enumerate(rows)]
    t=Table(cells,colWidths=[88,220,A4[0]-68-88-220])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#15283F')),
      ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#F4F6F9'),colors.white]),
      ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),
      ('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),7),
      ('BOTTOMPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#DBE3EC'))]))
    story += [t,Spacer(1,11),
      Paragraph('<b>Observed in the local E2B study</b>',styles['body']),
      Paragraph('First run_code P50: <b>1,027 ms</b>; second: <b>68 ms</b> (60 paired sandboxes). '
                'At a target of 256 resident sandboxes, 5 preparation attempts yielded <b>2 measured runs and 3 failures</b>. '
                'Readiness timeouts coincided with CPU bursts; a unique cause and hard capacity limit remain unproven.',styles['small']),
      Paragraph('<b>Technical distinctions:</b> Firecracker runs ordinary syscalls in its guest Linux kernel; per-syscall interception '
                'is not universal. Pause/resume preserves state and is not a clean reset. CPU-quota throttling and OOM were not '
                'established, and this VM had no swap. Compatibility examples depend on deployment.',styles['small']),
      Paragraph('<b>Scope:</b> local Firecracker deployment, minimal Code Interpreter template, short synthetic tests. '
                'No real-RL training speedup was measured. Validate workload correctness, timing boundaries and generator headroom before attribution.',styles['small'])]
    def footer(c,d):
        c.setStrokeColor(colors.HexColor('#DBE3EC'));c.line(34,27,A4[0]-34,27)
        c.setFont('DV',7.2);c.setFillColor(colors.HexColor('#5D7187'))
        c.drawString(34,16,'Sources: benchmark/REPORT.md; application, lifecycle and startup-failure reports | 2026-09-20')
        c.drawRightString(A4[0]-34,16,str(d.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)

def main():
    make_slides();make_report()
    (OUT/'Sandbox_Bottlenecks_Report_EN.md').write_text(REPORT)
    sources=['REPORT.md','phase2-sdk/APPLICATION-RESULTS.md','phase1-service/LIFECYCLE-RESULTS.md',
             'phase1-service/results/setup-failure-log-20260920.md','phase0-environment/results/inventory.json']
    (OUT/'sources.json').write_text(json.dumps({'created_utc':datetime.now(timezone.utc).isoformat(),
       'scope':'Mechanism-first English rewrite; illustrative examples distinguished from measured observations.',
       'sources':[{'path':'../../'+p,'sha256':hashlib.sha256((BASE/p).read_bytes()).hexdigest()}for p in sources]},indent=2)+'\n')
    print('Generated English bottleneck deck and report.')

if __name__=='__main__':main()

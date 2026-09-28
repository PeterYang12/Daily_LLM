#!/usr/bin/env python3
"""Rebuild the English evidence-led deck from local benchmark summaries only."""
from pathlib import Path
from datetime import datetime, timezone
import json, hashlib, csv
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import MSO_AUTO_SIZE, MSO_ANCHOR, PP_ALIGN
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_LABEL_POSITION, XL_MARKER_STYLE, XL_TICK_MARK
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.chart.datalabel import DataLabels
from pptx.oxml.xmlchemy import OxmlElement
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

OUT=Path(__file__).resolve().parent
BASE=OUT.parents[1]
FONT='Aptos'
W,H=13.333333,7.5
C={'bg':'F5F7FA','white':'FFFFFF','navy':'172B44','text':'344A60','muted':'64758A','line':'DCE4EC',
   'blue':'2362AF','teal':'087F80','gray':'AFBDCD','pale':'EAF1FA','mint':'E4F3EF','orange':'B66324','sand':'FFF1E4'}
pdfmetrics.registerFont(TTFont('DV','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
pdfmetrics.registerFont(TTFont('DV-Bold','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
AUDIT=[]
SOURCE_FILES=[
 'REPORT.md','README.md','../../RL/uni-agent-kickoff/10-local-e2b-vm-k8s/README.md','phase0-environment/REPORT.md','phase0-environment/CLOCK-ALIGNMENT.md',
 'phase0-environment/results/inventory.json','phase0-environment/results/file-transport-probe.json',
 'phase1-service/MAIN-RESULTS.md','phase1-service/MULTIGENERATOR-RESULTS.md',
 'phase1-service/IDLE-POOL-RESULTS.md','phase1-service/LIFECYCLE-RESULTS.md',
 'phase1-service/results/main-matrix/summary.json','phase1-service/results/multigenerator/summary.json',
 'phase1-service/results/idle-pool-summary.json','phase1-service/results/setup-failure-log-20260920.json',
 'phase2-sdk/MAIN-RESULTS.md','phase2-sdk/SUPPLEMENTAL-RESULTS.md','phase2-sdk/APPLICATION-RESULTS.md',
 'phase2-sdk/results/application/summary.json']

def read(rel):return json.loads((BASE/rel).read_text())
def rgb(c):return RGBColor.from_string(C.get(c,c))
def clean(sh):
    for el in sh._element.findall('{http://schemas.openxmlformats.org/presentationml/2006/main}style'): sh._element.remove(el)
    sh._element.spPr.append(OxmlElement('a:effectLst'))

def rect(s,x,y,w,h,fill='white',border=None,radius=False):
    sh=s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE, Inches(x),Inches(y),Inches(w),Inches(h))
    sh.fill.solid();sh.fill.fore_color.rgb=rgb(fill)
    if border:sh.line.color.rgb=rgb(border);sh.line.width=Pt(.9)
    else:sh.line.fill.background()
    if radius:sh.adjustments[0]=.05
    clean(sh);return sh

def wrap(v,w,sz,bold):
    out=[]
    for para in v.split('\n'):
        line=''
        for word in para.split():
            candidate=(line+' '+word).strip()
            if line and pdfmetrics.stringWidth(candidate,'DV-Bold' if bold else 'DV',sz)>w*72-.5:
                out.append(line);line=word
            else:line=candidate
        out.append(line)
    return '\n'.join(out)

def txt(s,v,x,y,w,h,sz=16,color='text',bold=False,align=PP_ALIGN.LEFT,do_wrap=True):
    if do_wrap:v=wrap(v,w,sz,bold)
    lines=v.split('\n');width=max([pdfmetrics.stringWidth(z,'DV-Bold' if bold else 'DV',sz) for z in lines],default=0)
    height=len(lines)*sz*1.16
    if width>w*72+.5 or height>h*72+1:
        raise ValueError(f'Overflow {v!r}: w {width:.1f}/{w*72:.1f} h {height:.1f}/{h*72:.1f}')
    AUDIT.append(dict(slide=len(PRS.slides),text=v,font_pt=sz,x=x,y=y,w=w,h=h,width_pt=width,height_pt=height))
    sh=s.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h));tf=sh.text_frame;tf.clear()
    tf.auto_size=MSO_AUTO_SIZE.NONE;tf.word_wrap=True;tf.vertical_anchor=MSO_ANCHOR.TOP
    tf.margin_left=tf.margin_right=tf.margin_top=tf.margin_bottom=0
    for i,line in enumerate(lines):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph();p.alignment=align;p.line_spacing=Pt(sz*1.16)
        p.space_before=p.space_after=Pt(0)
        run=p.add_run();run.text=line;run.font.name=FONT;run.font.size=Pt(sz);run.font.bold=bold;run.font.color.rgb=rgb(color)
        run._r.get_or_add_rPr().set('lang','en-US')
    return sh

def arrow(s,x1,y1,x2,y2,color='blue',end=True,width=1.6):
    sh=s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1),Inches(y1),Inches(x2),Inches(y2))
    sh.line.color.rgb=rgb(color);sh.line.width=Pt(width);clean(sh)
    if end:
        el=OxmlElement('a:tailEnd');el.set('type','triangle');el.set('w','sm');el.set('len','sm');sh._element.spPr.get_or_add_ln().append(el)
    return sh

def base(index,section,title,subtitle,sources):
    s=PRS.slides.add_slide(PRS.slide_layouts[6]);s.background.fill.solid();s.background.fill.fore_color.rgb=rgb('bg')
    rect(s,.52,.35,.055,.22,'blue')
    txt(s,section.upper(),.72,.35,11.5,.25,10.7,'blue',True)
    txt(s,title,.55,.81,12.25,.62,29,'navy',True)
    txt(s,subtitle,.57,1.47,12.10,.50,15,'muted')
    rect(s,.55,7.03,12.23,.012,'line')
    txt(s,sources,.57,7.13,11.8,.22,8.7,'muted')
    txt(s,f'{index} / 6',12.10,7.13,.65,.25,10,'muted',align=PP_ALIGN.RIGHT)
    return s

def takeaway(s,v):
    rect(s,.55,6.42,12.23,.42,'navy',radius=True)
    txt(s,v,.78,6.51,11.77,.26,13.5,'white',True)

def chart_style(ch,maxv,major,numfmt='0',legend=False):
    ch.has_title=False;ch.chart_style=10
    ch.font.name=FONT;ch.font.size=Pt(12)
    ch.has_legend=legend
    if legend:
        ch.legend.position=XL_LEGEND_POSITION.TOP;ch.legend.include_in_layout=False;ch.legend.font.name=FONT;ch.legend.font.size=Pt(12)
    va=ch.value_axis;va.minimum_scale=0;va.maximum_scale=maxv;va.major_unit=major
    va.tick_labels.font.name=FONT;va.tick_labels.font.size=Pt(11);va.tick_labels.font.color.rgb=rgb('muted');va.tick_labels.number_format=numfmt
    va.major_tick_mark=XL_TICK_MARK.NONE;va.minor_tick_mark=XL_TICK_MARK.NONE;va.format.line.fill.background()
    va.has_major_gridlines=True;va.major_gridlines.format.line.color.rgb=rgb('line');va.major_gridlines.format.line.width=Pt(.65)
    ca=ch.category_axis;ca.tick_labels.font.name=FONT;ca.tick_labels.font.size=Pt(12);ca.tick_labels.font.color.rgb=rgb('text')
    ca.major_tick_mark=XL_TICK_MARK.NONE;ca.minor_tick_mark=XL_TICK_MARK.NONE;ca.format.line.color.rgb=rgb('line')
    for el in ch._chartSpace.xpath('.//c:spPr/a:effectLst'):pass
    return ch

def labels(plot,fmt='0',size=13):
    plot.has_data_labels=True;dl=plot.data_labels;dl.position=XL_LABEL_POSITION.OUTSIDE_END
    dl.number_format=fmt;dl.font.name=FONT;dl.font.size=Pt(size);dl.font.bold=True;dl.font.color.rgb=rgb('navy')
    dl.show_value=True;dl.show_category_name=False;dl.show_series_name=False;dl.show_legend_key=False
    # Prevent split digit groups in narrow native-chart label boxes.
    for n in plot._element.xpath('.//c:dLbls/c:txPr/a:bodyPr'):n.set('wrap','none')

def notes(s,body,refs):
    s.notes_slide.notes_text_frame.text=body+'\n\nSources (relative to benchmark root):\n'+'\n'.join(refs)+'\n\n'+(
      'Study scope: local synthetic Phase 0/1/2 tests. Main matrix: 104 valid performance runs and 3 setup failures. '
      '42 supplemental runs and 6 multi-process cohorts are separate. Most data-plane windows are 20–30 seconds, '
      'usually 3 repeats; target N256 has only 2 valid repeats. No real-RL speedup, sustained capacity or SLO certification. '
      'Client timing uses monotonic clocks. Host/VM UTC alignment has about 1-second uncertainty, so precise cross-layer '
      'CPU attribution is not supported. This presentation makes no service calls.')

multi=read('phase1-service/results/multigenerator/summary.json')
configs={(d['adapter'],d['generator_count']):d for d in multi['configurations']}
churn=sorted([r for r in read('phase1-service/results/main-matrix/summary.json') if r['scenario']=='create_delete_churn' and r['mode']=='closed_loop'],key=lambda r:r['concurrency'])
assert [r['concurrency'] for r in churn]==[1,2,3,8]
app=read('phase2-sdk/results/application/summary.json');stages={r['operation']:r for r in app['stage_summary']}
probe=read('phase0-environment/results/file-transport-probe.json')
entity=next(o['raw_http_entity_bytes'] for o in probe['observations'] if o['transport']=='native_httpx_raw' and o['payload']=='repeated_x' and o['accept_encoding_mode']=='inherited_default')
assert entity==1055
DATA={'multigenerator':multi['configurations'],'churn':churn,'application':app,'file_check':{'logical_bytes':1048576,'repeated_x_gzip_entity_bytes':entity},
      'data_scope':'Previously completed experiments; no new service or SDK operations.'}

PRS=Presentation();PRS.slide_width=Inches(W);PRS.slide_height=Inches(H)
PRS.core_properties.title='CPU Sandbox Benchmarks: Evidence and Bottlenecks'
PRS.core_properties.subject='Phase 0/1/2 results, seven bottleneck cases, and local E2B architecture'
PRS.core_properties.author='Sandbox benchmark study'

# 1 — Calibration and client-side evidence.
s=base(1,'Benchmark evidence / Phase 0 controls + Phase 1/2 cross-check',
       'The client changed the measured throughput',
       'Same service, same total load: 64 concurrent commands across 64 sandboxes.',
       'Sources: Phase 0 REPORT; Phase 1/2 MULTIGENERATOR-RESULTS · 20 Sep 2026')
rect(s,.55,2.01,8.04,4.12,'white','line',True)
txt(s,'Successful commands / second',.81,2.22,7.5,.30,16,'navy',True)
d=CategoryChartData();d.categories=['HTTPX / Connect','Public SDK']
for g in [1,4]:d.add_series(f'{g} client process'+('es' if g>1 else ''),[configs[(a,g)]['common_successful_ops_s_median'] for a in ['service','sdk']])
ch=s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED,Inches(.80),Inches(2.65),Inches(7.49),Inches(2.79),d).chart
chart_style(ch,3000,1000,'#,##0',True);ch.plots[0].gap_width=85
for sr,col in zip(ch.series,['gray','blue']):sr.format.fill.solid();sr.format.fill.fore_color.rgb=rgb(col);sr.format.line.fill.background()
labels(ch.plots[0],'#,##0',14)
txt(s,'6.22× with 4 processes',1.21,5.59,3.3,.33,17,'blue',True,PP_ALIGN.CENTER)
txt(s,'1.26× with 4 processes',4.89,5.59,3.3,.33,17,'blue',True,PP_ALIGN.CENTER)
rect(s,8.84,2.01,3.94,4.12,'mint',radius=True)
txt(s,'PHASE 0 · FILE CHECK',9.08,2.23,3.44,.26,11.5,'teal',True)
txt(s,'1 MiB',9.08,2.73,3.44,.63,32,'navy',True)
txt(s,'logical file size',9.08,3.34,3.44,.28,15,'text')
txt(s,'1,055 bytes',9.08,3.85,3.44,.54,27,'teal',True)
txt(s,'HTTP entity with repeated-x / gzip',9.08,4.48,3.38,.55,15,'text')
txt(s,'Identity encoding: 1,048,576 bytes.\nCount logical and transferred bytes separately.',9.08,5.20,3.42,.72,12.5,'text')
txt(s,'3-run medians. Processes, connection pools and warmup coverage changed together; service configuration was unchanged.',
    .68,6.16,12.0,.20,9.3,'muted')
takeaway(s,'Before estimating service capacity, validate the client load generator and the payload.')
notes(s,
      'The main chart is a cross-check from Phase 1/2, not a Phase 0-only result. It shows exec_true at global C=N64, '
      'one process versus four C16/N16 processes, with unchanged server configuration. Throughput is the median of '
      '3 common-window successful-operation rates. Exact HTTPX/Connect values: 312.90 and 1946.95 ops/s; SDK: 1956.60 and '
      '2474.54 ops/s. Ratios are 6.22x and 1.26x. Processes, transport pools, Python execution and warmup coverage changed '
      'together. This proves measurement sensitivity to client organization, not a service optimization or isolated CPU effect. '
      'The four-process cohorts retained 5 protocol failures and 26 SDK failures in full worker accounting; common-window '
      'counts may omit boundary errors.\nThe Phase 0 inset is a separate file calibration. Repeated-x file logical length is '
      '1048576 bytes; gzip raw HTTP entity is 1055 bytes, identity is 1048576, random/gzip is 1048919. Entity excludes headers '
      'and TCP overhead. SDK/pyqwest may decompress before HTTPX byte counting. These reads cannot be labeled network bandwidth. '
      'Random/identity workload comparisons changed both encoding and content, so they do not isolate gzip cost.',
      ['phase0-environment/REPORT.md','phase0-environment/results/file-transport-probe.json','phase1-service/MULTIGENERATOR-RESULTS.md','phase1-service/results/multigenerator/summary.json'])

# 2 — Service lifecycle scaling.
s=base(2,'Benchmark evidence / Phase 1 service',
       'More parallel creates increased waiting',
       'Create → delete loop; concurrency is the number of loop workers. Service startup admission stayed at 3.',
       'Sources: Phase 1 MAIN-RESULTS; setup failure log · Medians of 3 runs per concurrency')
for x,title,color,key,scale,maxv,major in [(.55,'Successful create-delete cycles / s','blue','successful_throughput_ops_s',1,7,2),
                                          (6.80,'Create latency P95 / seconds','orange','p95_ms',.001,3.5,1)]:
    rect(s,x,2.00,5.98,3.39,'white','line',True)
    txt(s,title,x+.25,2.21,5.48,.31,16,'navy',True)
    xy=XyChartData();series=xy.add_series(title)
    for r in churn:series.add_data_point(r['concurrency'],r[key]*scale)
    ch=s.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER_LINES,Inches(x+.18),Inches(2.73),Inches(5.55),Inches(2.24),xy).chart
    chart_style(ch,maxv,major,'0.0' if scale<1 else '0')
    ca=ch.category_axis;ca.minimum_scale=0;ca.maximum_scale=9;ca.major_unit=1
    ca.tick_labels.number_format='0';ca.tick_labels.font.size=Pt(11)
    sr=ch.series[0];sr.format.line.color.rgb=rgb(color);sr.format.line.width=Pt(2.4)
    sr.marker.style=XL_MARKER_STYLE.CIRCLE;sr.marker.size=6;sr.marker.format.fill.solid();sr.marker.format.fill.fore_color.rgb=rgb(color);sr.marker.format.line.color.rgb=rgb(color)
    dle=OxmlElement('c:dLbls');sr._element.insert(list(sr._element).index(sr._element.find('{http://schemas.openxmlformats.org/drawingml/2006/chart}xVal')),dle);dl=DataLabels(dle);dl.position=XL_LABEL_POSITION.ABOVE;dl.show_value=True;dl.font.name=FONT;dl.font.size=Pt(12);dl.font.bold=True;dl.font.color.rgb=rgb(color);dl.number_format='0.00'
    dl.show_series_name=False;dl.show_category_name=False;dl.show_legend_key=False;dl.show_percentage=False
    txt(s,'Concurrent loop workers',x+.5,5.02,5.0,.23,11.5,'muted',align=PP_ALIGN.CENTER)
rect(s,.55,5.59,12.23,.63,'sand',radius=True)
txt(s,'Target pool: 256 sandboxes',.80,5.77,4.1,.29,15,'orange',True)
txt(s,'2 of 5 preparation attempts reached measurement; 3 failed.',5.0,5.77,7.5,.31,15,'text',True)
takeaway(s,'C3 → C8: create P95 rose 5.4×; throughput changed from 5.88 to 5.47 cycles/s.')
notes(s,
      'The throughput chart includes both creation and deletion; the latency chart times create only. Numeric x-axis preserves '
      'spacing of tested concurrency 1,2,3,8; connecting segments guide the eye rather than predicting untested levels. '
      'Each point is the median of 3 run-level statistics, 100 cycles/run. Successful throughput: 3.64,5.51,5.88,5.47 cycles/s. '
      'Create P95:455.28,395.40,537.11,2914.65ms. P95 is the latency below which 95% of successful creates fall in each run. '
      'Startup/restore admission 3 is a configured simultaneous-start limit, not 3/s; no admission-parameter A/B establishes '
      'it as the unique bottleneck.\nThe target256 callout comes from a separate resident-pool preparation experiment at tool '
      'concurrency8. Across5 attempts,2 reached measurement and3 failed before measurement. One involved the initial Firecracker '
      'metrics/API Unix socket connection,2 involved envd readiness timeouts after snapshot load. Failures are not0-ops/s points '
      'and do not establish a hard capacity cap. A separate <=1-create/s slow ramp failed on request117 after116 successes, '
      'so slowing arrival alone has not been demonstrated as a fix.',
      ['phase1-service/MAIN-RESULTS.md','phase1-service/results/main-matrix/summary.json','phase1-service/results/setup-failure-log-20260920.json','phase1-service/IDLE-POOL-RESULTS.md'])

# 3 — Application latency and first-call reliability.
s=base(3,'Benchmark evidence / Phase 2 public SDK',
       'The first useful code result took 2.16 seconds',
       '60 fresh sandboxes, 3 runs, one at a time; local minimal Python / JavaScript Code Interpreter template.',
       'Sources: Phase 2 APPLICATION-RESULTS and MAIN-RESULTS · Application: pooled P50 of 60 observations')
rect(s,.55,2.01,7.65,3.48,'white','line',True)
txt(s,'SDK call duration · P50, milliseconds',.80,2.22,7.15,.32,16,'navy',True)
d=CategoryChartData();d.categories=['Second code','First code','Command + curl','Create']
d.add_series('P50 ms',[stages[o]['p50_ms'] for o in ['run_code_second','run_code_first','health_check','create']])
ch=s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED,Inches(.77),Inches(2.68),Inches(7.05),Inches(2.45),d).chart
chart_style(ch,1200,400,'#,##0');ch.plots[0].gap_width=65
for p,col in zip(ch.series[0].points,['teal','blue','gray','gray']):p.format.fill.solid();p.format.fill.fore_color.rgb=rgb(col);p.format.line.fill.background()
labels(ch.plots[0],'#,##0',14)
rect(s,8.45,2.01,4.33,3.48,'mint',radius=True)
txt(s,'MEASURED END TO END',8.71,2.23,3.8,.26,11.5,'teal',True)
txt(s,'2.16 s',8.71,2.69,3.8,.67,36,'navy',True)
txt(s,'Create → health command\n→ first code result',8.71,3.49,3.78,.65,17,'text',True)
txt(s,'Second code call was faster in 60/60 pairs; median paired ratio: 15.06×.',8.71,4.46,3.72,.75,15,'teal',True)
rect(s,.55,5.68,12.23,.55,'sand',radius=True)
txt(s,'Separate C64 command test:',.78,5.83,3.45,.29,14.5,'orange',True)
txt(s,'first measured call 18/192 failed (9.38%); later calls 1/171,318 failed.',4.16,5.83,8.34,.30,14.1,'text',True)
takeaway(s,'Prioritize first-use latency and connection readiness in short agent tasks.')
notes(s,
      'Application sequence:Sandbox.create -> commands.run(curl /health) -> run_code(Hello World) -> second run_code -> kill. '
      'Each stage has60 observations from3 runs of20 fresh sandboxes at C1/N1. Calls include returned/consumed output streams. '
      'CreateP50=269.60ms, health command/curl=835.74ms, firstcode=1026.74ms, secondcode=68.39ms. P50s pool60 observations; '
      'they are not run-median aggregations. Paired create-start->first-code-endP50=2155.13ms includes the health command and '
      'inter-stage gaps; it was calculated from each timeline, not by adding stage medians. All60 second calls were faster; '
      'median paired ratio15.06. This is a local minimal Python/JS template with official server/kernels, not full cloud template '
      'coverage. First-use cost is measured; SDK, syscalls and process startup are not isolated.\nThe failure strip is a '
      'separate single-process exec_true C=N64 main-matrix experiment, original warmupslot0 only. First measurement request '
      'per worker:18/192; later:1/171318; overall19/171510. First measured does not mean first-ever SDK operation. '
      'All-slot command warmup at C8 still left9 first-call failures at C64, without proving that every physical connection '
      'had been established. The root cause remains unresolved.',
      ['phase2-sdk/APPLICATION-RESULTS.md','phase2-sdk/results/application/summary.json','phase2-sdk/MAIN-RESULTS.md','phase2-sdk/SUPPLEMENTAL-RESULTS.md'])

# 4 — Evidence to mechanism, priorities.
s=base(4,'Bottleneck analysis / Cases 2, 6 and 1',
       'First-use cost and startup reliability come first',
       'Observed latency and failures set the priority; the underlying mechanism still needs a controlled test.',
       'Sources: Phase 2 APPLICATION-RESULTS; Phase 1 MAIN-RESULTS and IDLE-POOL-RESULTS')
txt(s,'CASE / EVIDENCE STATUS',.77,2.00,3.0,.26,11,'muted',True)
txt(s,'WHAT THE DATA SHOW',4.02,2.00,4.2,.26,11,'muted',True)
txt(s,'NEXT EXPERIMENT',8.55,2.00,3.95,.26,11,'muted',True)
rows=[
 ('2','Cold start / reset','First-use cost observed','1,027 → 68 ms for code calls.\nClean reset was not isolated.','Compare ready-pool reuse with fresh reset. Trace envd and first-process readiness.','teal'),
 ('6','Resource contention','Correlation observed','Slow ramp: envd startup timed out.\nCPU peak: 133/192 cores.\nCPU waiting (10 s avg): 22.4% peak.','Trace startup threads under steady and burst arrivals, with client headroom fixed.','orange'),
 ('1','CPU throttling','Not established','0 recorded cgroup throttle increments in the slow ramp.','Check CPU-time quotas and throttle counters while reproducing latency spikes.','muted')]
for i,(num,title,status,evidence,action,color) in enumerate(rows):
    y=2.40+i*1.25;rect(s,.55,y,12.23,1.10,'white','line',True)
    rect(s,.75,y+.18,.36,.36,color,radius=True);txt(s,num,.75,y+.23,.36,.21,12,'white',True,PP_ALIGN.CENTER)
    txt(s,title,1.25,y+.12,2.47,.61,18,'navy',True)
    txt(s,status,1.25,y+.80,2.50,.22,11.4,color,True)
    txt(s,evidence,4.02,y+.18,4.15,.80,15.3,'text')
    txt(s,action,8.55,y+.18,3.96,.80,15.3,'text')
takeaway(s,'RL implication: repeated setup and launch bursts can delay an entire rollout batch.')
notes(s,
      'Case numbers match the user-provided seven-category list. Cold start/reset: measured first-use cost and startup failures '
      'support prioritizing readiness, but no independent clean-reset latency was measured. Pause/resume preserves state. '
      'Ready-pool reuse is a proposed experiment, not a measured optimization.\nResource contention: separate slow-ramp case '
      'had VM CPU peak133.46/192 cores and CPU PSI some avg10 peak22.43%. PSI is waiting/scheduling pressure rather than CPU '
      'utilization. The peaks need not occur at the same sample. Snapshot load completed before envd timed out. The broader '
      'formal envd incidents also showed CPU bursts, but their causal origin is unresolved. Timing/capture delays limit precise '
      'cross-layer attribution. The load-generator cross-check on slide1 independently establishes client organization affects '
      'measured throughput.\nCPU throttling:0 recorded cgroup throttle increments in the slow-ramp scope does not exclude '
      'all quota effects under other tasks. vCPU count and512-sandbox count quota are not CPU-time quotas. Proposed counters '
      'include cpu.max,nr_throttled,throttled_usec.\nThe rollout-batch statement is a plausible RL implication, not a measured '
      'real-RL result.',
      ['phase2-sdk/APPLICATION-RESULTS.md','phase1-service/IDLE-POOL-RESULTS.md','phase1-service/results/idle-pool-summary.json','phase1-service/MAIN-RESULTS.md'])

# 5 — Mechanisms that remain unisolated.
s=base(5,'Bottleneck analysis / Cases 3, 4, 5 and 7',
       'The other four cases need targeted measurements',
       'Use the current observations to design the next tests; avoid assigning causes from end-to-end latency alone.',
       'Sources: Phase 0 REPORT; Phase 1 IDLE-POOL / LIFECYCLE results; Phase 2 APPLICATION-RESULTS')
txt(s,'CASE',.80,2.00,2.8,.26,11,'muted',True)
txt(s,'CURRENT EVIDENCE',3.94,2.00,4.1,.26,11,'muted',True)
txt(s,'TEST THAT WOULD ISOLATE IT',8.44,2.00,4.05,.26,11,'muted',True)
rows=[
 ('3','System calls /\nprocess creation','Full command/code calls were timed; syscall and launch costs remain unisolated.','Compare many tiny commands with one batch; trace fork/exec and syscalls.'),
 ('4','Filesystem I/O','1 MiB → 1,055 entity bytes with gzip. Storage saturation is not established.','Fix encoding and content; test small files, metadata, and cold/warm caches.'),
 ('5','Memory pressure','Slow ramp: 1,464 GiB still available in the outer VM. Guest-local pressure is untested.','Grow guest working sets; record reclaim, memory pressure and OOM events.'),
 ('7','Compatibility','240/240 application stages validated; 120 pause/resume rounds preserved state.','Preflight RL packages, debuggers and required kernel/device capabilities.')]
for i,(num,title,ev,act) in enumerate(rows):
    y=2.38+i*.94;rect(s,.55,y,12.23,.86,'white','line',True)
    rect(s,.76,y+.17,.34,.34,'muted',radius=True);txt(s,num,.76,y+.21,.34,.22,12,'white',True,PP_ALIGN.CENTER)
    txt(s,title,1.24,y+.15,2.43,.62,16.6,'navy',True)
    txt(s,ev,3.94,y+.10,4.05,.71,14.5,'text')
    txt(s,act,8.44,y+.10,4.06,.71,14.5,'text')
takeaway(s,'Next workload: short commands, small-file activity, resets, and controlled working-set growth.')
notes(s,
      'System calls/process creation: full SDK calls include transport, process launch, execution, pipes and result handling. '
      'No syscall/fork/exec profile isolates their costs. Firecracker ordinary Linux syscalls run in the guest kernel; there '
      'is no universal per-syscall security proxy.\nFilesystem: compression/calibration and file API behavior were measured, '
      'not a proven storage-device or copy-on-write bottleneck. Reads do not inherently trigger COW; writes/updates can. '
      'Payload/encoding controls must be fixed before comparing caches or storage.\nMemory: slow-ramp outer-VM minimum '
      'MemAvailable1464.17GiB, swap disabled. This argues against outer-VM RAM exhaustion in that incident; guest-local '
      'limits and future RL working sets remain untested.\nCompatibility:240 validated application stages were across60 '
      'local minimal Python/JS sandboxes.120 pause/resume rounds used60 rounds per adapter but only3 independent lineages '
      'per adapter. Checked file/process state survived. These are limited positive functional observations, not comprehensive '
      'language/package/debugger/device support. Initial permission/path preflight failures were corrected separately. '
      'Compatibility limitations can be functional failures rather than performance bottlenecks.',
      ['phase0-environment/REPORT.md','phase1-service/IDLE-POOL-RESULTS.md','phase1-service/LIFECYCLE-RESULTS.md','phase2-sdk/APPLICATION-RESULTS.md'])

# 6 — Infrastructure and basic configuration.
s=base(6,'CPU sandbox infrastructure / Tested configuration',
       'One E2B backend, many Firecracker microVMs',
       'Single-node E2B Embed on K3s inside a QEMU/KVM VM; gateway routes control and sandbox traffic.',
       'Sources: Phase 0 inventory and study configuration · Snapshot: 20 Sep 2026')
# Left: request routes. All boxes and arrows are editable PowerPoint objects.
rect(s,.55,2.02,7.58,4.78,'white','line',True)
rect(s,.78,3.00,7.10,3.52,'pale','line',True)
txt(s,'OUTER VM · Ubuntu 24.04 / K3s',3.24,3.14,4.31,.27,11.5,'blue',True)
rect(s,1.00,2.23,2.12,.54,'navy',radius=True)
txt(s,'Client / SDK',1.12,2.36,1.88,.27,13.2,'white',True,PP_ALIGN.CENTER)
arrow(s,2.06,2.79,2.06,3.70)
txt(s,'10.145.64.67:8088',3.35,2.39,3.93,.26,12.2,'muted')
rect(s,1.01,3.75,2.05,.78,'white','blue',True)
txt(s,'e2b-gateway',1.12,3.91,1.83,.27,15,'navy',True,PP_ALIGN.CENTER)
txt(s,'Nginx',1.12,4.24,1.83,.19,10.5,'muted',align=PP_ALIGN.CENTER)
rect(s,3.56,3.62,4.03,1.50,'white','line',True)
txt(s,'e2b-0 · one Pod',3.79,3.74,3.55,.26,12.5,'navy',True)
rect(s,3.81,4.14,.91,.39,'pale',radius=True)
txt(s,'API',3.87,4.21,.79,.23,12.5,'blue',True,PP_ALIGN.CENTER)
rect(s,5.21,4.14,2.12,.39,'pale',radius=True)
txt(s,'Orchestrator',5.28,4.21,1.98,.23,12.5,'blue',True,PP_ALIGN.CENTER)
arrow(s,4.74,4.33,5.18,4.33)
rect(s,3.81,4.67,2.01,.31,'mint',radius=True)
txt(s,'Client proxy',3.88,4.72,1.87,.21,11.6,'teal',True,PP_ALIGN.CENTER)
arrow(s,3.07,4.06,3.36,4.06,end=False);arrow(s,3.36,4.06,3.36,4.33,end=False);arrow(s,3.36,4.33,3.77,4.33)
arrow(s,3.07,4.31,3.27,4.31,'teal',False);arrow(s,3.27,4.31,3.27,4.82,'teal',False);arrow(s,3.27,4.82,3.77,4.82,'teal')
# Paths from the Pod into the microVMs.
arrow(s,6.63,4.54,6.63,5.58,'blue')
txt(s,'create / restore',6.76,5.28,1.04,.39,10.6,'blue')
arrow(s,4.39,5.00,4.39,5.58,'teal')
txt(s,'command / file',3.02,5.29,1.23,.40,10.6,'teal')
rect(s,4.06,5.78,3.31,.59,'white','line',True)
rect(s,3.94,5.70,3.31,.59,'white','line',True)
rect(s,3.82,5.62,3.31,.62,'navy',radius=True)
txt(s,'Firecracker microVMs',3.96,5.74,3.02,.24,14,'white',True,PP_ALIGN.CENTER)
txt(s,'guest Linux + envd',3.96,6.02,3.02,.19,10.7,'white',align=PP_ALIGN.CENTER)
txt(s,'e2b-0: 8 running service containers. Each sandbox is a Firecracker process.',.80,6.62,7.08,.19,9.7,'muted')
# Right: four compact config groups.
for y,head,body in [
 (2.05,'PHYSICAL HOST','2× AMD EPYC 9965 · 384 cores\nSMT off; QEMU on CPUs 192–383'),
 (3.23,'OUTER VM','192 vCPU · ~1,487 GiB visible RAM\n1 TiB qcow2 disk on /data2'),
 (4.41,'TYPICAL SANDBOX','2 vCPU · 4 GiB RAM\n4 KiB backing; HugeTLB pool = 0'),
 (5.59,'CONFIGURED LIMITS','512 running sandboxes (count quota)\n3 concurrent starts / restores')]:
    rect(s,8.40,y,4.38,1.03,'white','line',True)
    txt(s,head,8.64,y+.15,3.9,.24,10.8,'blue',True)
    txt(s,body,8.64,y+.47,3.90,.53,13.5,'text')
notes(s,
      'Architecture is a schematic of the tested deployment. SDK/client accesses host LAN endpoint10.145.64.67:8088, forwarded '
      'to the outer VM gateway. Nginx gateway routes control requests to e2b-0API, then orchestrator creates/restores Firecracker '
      'microVMs. Command/file traffic routes through client-proxy to target microVM envd. It currently routes to one E2B backend; '
      'this is not an HA multi-backend load-balanced deployment.\ne2b-0 is one StatefulSet with one Pod. READY8/8 means '
      '8 running service containers:api,orchestrator,client-proxy,postgres,redis,clickhouse,vector,ready. Terminated init/setup '
      'containers are also present in inventory. Auxiliary service containers are omitted from the main request diagram. '
      'A sandbox is a Firecracker process/microVM, not a Kubernetes Pod.\nHost:2xAMD EPYC9965,192cores/socket,384physicalcores, '
      'SMT off. Generator CPUs0–15 and analysis16–31 are on socket0; QEMU affinity192–383 on socket1. CPU affinity does not '
      'eliminate shared memory/cache/I/O effects. Outer QEMU VM uses systemd rather than libvirt; configured RAM1511GiB, '
      'Linux visible1486.84GiB;192vCPU;1TiB qcow2 on/data2. Ordinary4KiB sandbox backing, HugeTLB pools0; no huge-page A/B '
      'was measured.\nTypical sandbox2vCPU/4096MiB. Configured team/node count quota512 and simultaneous startup/restore '
      'admission3 are separate controls; neither is measured sustainable capacity nor CPU-time bandwidth quota. '
      'NBD devices1024; neighbor thresholds4096/8192/16384. SDK e2b2.49.1 and Code Interpreter2.10.0. '
      'Runtime local-4k-512-20260920; authoritative deployed binary hashes are in inventory. OTel is incomplete and '
      'independent observers can emit empty-endpoint export warnings; external finite resource sampling was used.',
      ['phase0-environment/results/inventory.json','phase0-environment/REPORT.md','REPORT.md','README.md','../../RL/uni-agent-kickoff/10-local-e2b-vm-k8s/README.md'])

path=OUT/'CPU_Sandbox_Benchmark_Data_EN.pptx';PRS.save(path)
(OUT/'chart_data.json').write_text(json.dumps(DATA,indent=2)+'\n')
(OUT/'layout-check.json').write_text(json.dumps({'slides':len(PRS.slides),'text_blocks':AUDIT},indent=2)+'\n')
(OUT/'slides_text.txt').write_text('\n\n'.join('SLIDE '+str(i+1)+'\n'+'\n'.join(sh.text for sh in sl.shapes if sh.has_text_frame) for i,sl in enumerate(PRS.slides))+'\n')
(OUT/'sources.json').write_text(json.dumps({'generated_utc':datetime.now(timezone.utc).isoformat(),'source_root':str(BASE),'source_files':[{'path':p,'sha256':hashlib.sha256((BASE/p).read_bytes()).hexdigest()} for p in SOURCE_FILES]},indent=2)+'\n')
print(path)

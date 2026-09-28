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
 'phase2-sdk/results/application/summary.json','phase2-sdk/code/official_sdk_baseline.py','phase2-sdk/results/application/frozen-support/template-smoke.json','phase1-service/code/runner.py']

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
DATA={'command_throughput':[configs[('service',g)] for g in [1,4]],'churn':churn,'application':app,
      'sdk_failure_cohort':configs[('sdk',1)],'data_scope':'Previously completed experiments; no new service or SDK operations.'}

PRS=Presentation();PRS.slide_width=Inches(W);PRS.slide_height=Inches(H)
PRS.core_properties.title='CPU Sandbox Benchmarks: Evidence and Bottlenecks'
PRS.core_properties.subject='Measured command, creation and first-use limits; observed failures; local E2B architecture'
PRS.core_properties.author='Sandbox benchmark study'

# 1 — One finding: the load-generator setup limited measured throughput.
s=base(1,'Benchmark result / Command throughput',
       'Our load generator limited the command test',
       'Task: repeatedly run a shell command that immediately exits (true). Same server in both tests.',
       'Source: Phase 1 MULTIGENERATOR-RESULTS · HTTPX / Connect · Median of 3 runs, 30 seconds each')
rect(s,.55,2.02,7.64,4.06,'white','line',True)
txt(s,'Successful commands per second',.82,2.25,7.05,.33,18,'navy',True)
d=CategoryChartData();d.categories=['1 client process','4 client processes']
d.add_series('Commands / s',[configs[('service',g)]['common_successful_ops_s_median'] for g in [1,4]])
ch=s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED,Inches(.89),Inches(2.85),Inches(6.96),Inches(2.85),d).chart
chart_style(ch,2500,500,'#,##0');ch.plots[0].gap_width=130
for pt,col in zip(ch.series[0].points,['gray','blue']):
    pt.format.fill.solid();pt.format.fill.fore_color.rgb=rgb(col);pt.format.line.fill.background()
labels(ch.plots[0],'#,##0',20)
rect(s,8.46,2.02,4.32,4.06,'mint',radius=True)
txt(s,'6.2×',8.77,2.39,3.68,.85,45,'teal',True)
txt(s,'higher throughput',8.77,3.28,3.68,.76,24,'navy',True)
txt(s,'Both tests used 64 sandboxes and 64 commands in flight.',8.77,4.27,3.65,1.0,18,'text')
txt(s,'Server configuration unchanged.',8.77,5.47,3.65,.31,13.5,'teal',True)
txt(s,'Load split: 1 process × 64 workers → 4 processes × 16 workers. Connection pools and warmup coverage changed too.',
    .70,6.14,12.0,.23,10.1,'muted')
takeaway(s,'The original load-generator setup capped the result. Validate client capacity before sizing the service.')
notes(s,
      'The task is /bin/bash -l -c true through HTTPX/Connect; exit code0 and empty stdout/stderr are validated. '
      'Global concurrency and resident count are both64. The single process has64 workers;4 processes have16 workers each. '
      'Three30-second repeats per condition. Median common-window successful throughput:312.90 and1946.95ops/s, ratio6.22. '
      'Server configuration was unchanged. This establishes a limit in the tested load-generator setup, not a proven GIL '
      'or CPU-only explanation:process count, transport pools and warmup coverage changed together. The4-process protocol '
      'cohorts retained5 failures in full worker accounting; throughput uses only successful common-window completions. '
      'This is a benchmark-method limitation, not a measured service optimization. SDK and file-encoding side stories '
      'have been removed from the visible slide to keep one finding.',
      ['phase1-service/MULTIGENERATOR-RESULTS.md','phase1-service/results/multigenerator/summary.json'])

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
txt(s,'Higher concurrency added latency',.80,5.77,6.0,.30,15,'orange',True)
txt(s,'No cycle throughput gain beyond 3 workers.',7.0,5.77,5.50,.31,15,'text',True)
takeaway(s,'C3 → C8: create P95 rose 5.4×; throughput changed from 5.88 to 5.47 cycles/s.')
notes(s,
      'The throughput chart includes both creation and deletion; the latency chart times create only. Numeric x-axis preserves '
      'spacing of tested concurrency 1,2,3,8; connecting segments guide the eye rather than predicting untested levels. '
      'Each point is the median of 3 run-level statistics, 100 cycles/run. Successful throughput: 3.64,5.51,5.88,5.47 cycles/s. '
      'Create P95:455.28,395.40,537.11,2914.65ms. P95 is the latency below which 95% of successful creates fall in each run. '
      'Startup/restore admission 3 is a configured simultaneous-start limit, not 3/s; no admission-parameter A/B establishes '
      'it as the unique bottleneck.\nThe target256 outcome on slide5 comes from separate, sequential resident-pool preparation; planned tool '
      'measurement concurrency was8. Across5 attempts,2 reached measurement and3 failed before measurement. One involved the initial Firecracker '
      'metrics/API Unix socket connection,2 involved envd readiness timeouts after snapshot load. Failures are not0-ops/s points '
      'and do not establish a hard capacity cap. A separate <=1-create/s slow ramp failed on request117 after116 successes, '
      'so slowing arrival alone has not been demonstrated as a fix.',
      ['phase1-service/MAIN-RESULTS.md','phase1-service/results/main-matrix/summary.json','phase1-service/results/setup-failure-log-20260920.json','phase1-service/IDLE-POOL-RESULTS.md'])

# 3 — One application finding, with the actual workload and configuration.
s=base(3,'Benchmark result / Python SDK',
       'The first Python call was about 15× slower',
       "Task: print('Hello, world!') — executed twice in the same sandbox.",
       'Source: Phase 2 APPLICATION-RESULTS · P50 of 60 first/repeat pairs · Full public SDK call time')
rect(s,.55,2.02,7.09,3.90,'white','line',True)
txt(s,'Python call latency · milliseconds',.82,2.24,6.54,.34,18,'navy',True)
d=CategoryChartData();d.categories=['Repeat call','First call']
d.add_series('P50 ms',[stages[o]['p50_ms'] for o in ['run_code_second','run_code_first']])
ch=s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED,Inches(.82),Inches(2.94),Inches(6.37),Inches(2.40),d).chart
chart_style(ch,1200,400,'#,##0');ch.plots[0].gap_width=80
for pt,col in zip(ch.series[0].points,['teal','blue']):
    pt.format.fill.solid();pt.format.fill.fore_color.rgb=rgb(col);pt.format.line.fill.background()
labels(ch.plots[0],'#,##0',20)
rect(s,7.92,2.02,4.86,3.90,'white','line',True)
txt(s,'TEST CONFIGURATION',8.20,2.26,4.30,.29,12,'blue',True)
for y,label,value in [
 (2.78,'Template','Local minimal Code Interpreter'),
 (3.28,'Runtime','Python 3.11.6'),
 (3.78,'Sandbox','2 vCPU / 4 GiB / 4 KiB pages'),
 (4.28,'Load','1 sandbox, 1 call at a time'),
 (4.78,'Samples','3 runs × 20 fresh sandboxes'),
 (5.28,'SDK','e2b 2.49.1 / CI 2.10.0')]:
    txt(s,label,8.20,y,1.14,.31,13.5,'muted',True)
    txt(s,value,9.39,y,3.10,.52,14.1,'text')
txt(s,'Per sandbox: create → curl /health → Python call → repeat Python call → kill',.72,6.10,11.94,.29,13.4,'text')
takeaway(s,'For this trivial task, first-use overhead dominates the execution time.')
notes(s,
      "Exact code:print('Hello, world!'). Health command:curl http://0.0.0.0:49999/health. "
      'Sequence:create -> commands.run(health command) -> run_code -> same run_code -> kill. '
      'Three runs of20 fresh sandboxes, C1/N1,60 first/repeat pairs. The first and repeat calls use the same sandbox. '
      'Local template benchmark-ci-minimal-20260920 retains pinned upstream server and Python/JS kernels, Python3.11.6; '
      '2vCPU/4096MiB/ordinary4KiB pages. e2b2.49.1,e2b-code-interpreter2.10.0. This is not the full cloud template. '
      'Displayed pooled P50s:1026.74ms and68.39ms, ratio15.01; median within-sandbox paired ratio15.06. '
      'About15x is valid for either descriptive comparison. Public SDK call timing includes consumed run_code streams; '
      'template build and cleanup are excluded. First-use cost is observed; SDK transport, guest process startup and '
      'interpreter initialization were not isolated as causes. Creation and health-command latency and the separate '
      'C64 reliability test have been removed from the visible slide to keep the workload and main finding clear. '
      'For reference:createP50269.60ms,health835.74ms,paired create-start to first-code-endP502155.13ms including health.',
      ['phase2-sdk/APPLICATION-RESULTS.md','phase2-sdk/results/application/summary.json','phase2-sdk/code/official_sdk_baseline.py','phase2-sdk/results/application/frozen-support/template-smoke.json','phase1-service/code/runner.py'])

# 4 — Only performance limitations actually measured in this study.
s=base(4,'Analysis / Observed performance limits',
       'Three measured limits to address',
       'Each optimization below targets a problem observed in these experiments.',
       'Evidence: slides 1–3 · Optimization directions are proposed tests, not validated fixes')
txt(s,'WHERE',.79,2.05,2.80,.27,11.5,'muted',True)
txt(s,'OBSERVED LIMIT',3.80,2.05,4.33,.27,11.5,'muted',True)
txt(s,'OPTIMIZATION DIRECTION',8.45,2.05,4.08,.27,11.5,'muted',True)
rows=[
 ('Benchmark client','1 → 4 client processes:\n313 → 1,947 commands/s.','Use multiple generators; verify client capacity at each load level.'),
 ('Creation path','Concurrency 3 → 8:\nP95 0.54 → 2.91 s;\nthroughput 5.88 → 5.47 cycles/s.','Trace time waiting vs. restoring; then test the startup admission setting.'),
 ('First Python call','Same code, same sandbox:\nfirst call 1,027 ms; repeat 68 ms.','Test reuse of a ready sandbox or interpreter context.')]
for i,(where,evidence,direction) in enumerate(rows):
    y=2.51+i*1.17;rect(s,.55,y,12.23,1.02,'white','line',True)
    txt(s,where,.80,y+.20,2.70,.64,18,'navy',True)
    txt(s,evidence,3.80,y+.14,4.31,.78,15.5,'text')
    txt(s,direction,8.45,y+.14,4.05,.78,15.5,'text')
takeaway(s,'Focus on the client limit, creation waiting, and first-use cost.')
notes(s,
      'This slide includes only observed performance-path limitations. It intentionally removes the former seven-category '
      'checklist and unobserved throttling, memory, filesystem, syscall and compatibility hypotheses. '
      'Client setup affects measured throughput at fixed global C=N64; splitting generators is an observed measurement '
      'improvement, not a service optimization. Creation concurrency3->8 increases createP95537.11->2914.65ms while '
      'create-delete throughput changes5.88->5.47cycles/s. Queue waiting vs restoration time is a proposed decomposition, '
      'not an already-isolated causal result; admission3 alone is not proven to be the unique limit. '
      'The first Python call costs1026.74ms versus repeat68.39ms in the local minimal Hello World scenario. Reuse of a ready '
      'sandbox/context is a proposed optimization to test against required task isolation; the benchmark did not establish '
      'a clean-reset latency or real-RL speedup.',
      ['phase1-service/MULTIGENERATOR-RESULTS.md','phase1-service/MAIN-RESULTS.md','phase2-sdk/APPLICATION-RESULTS.md'])

# 5 — Only failures actually encountered; separate failure stage from root cause.
s=base(5,'Analysis / Observed startup and connection failures',
       'Startup and first measured calls failed',
       'These failure points were observed; their underlying causes remain unresolved.',
       'Sources: Phase 1 setup-failure log; Phase 2 MAIN-RESULTS · Separate experiments and denominators')
rect(s,.55,2.02,5.98,4.08,'white','line',True)
rect(s,6.80,2.02,5.98,4.08,'white','line',True)
txt(s,'POOL PREPARATION',.83,2.27,5.41,.27,12,'blue',True)
txt(s,'3 of 5 attempts failed',.83,2.81,5.41,.55,25,'orange',True)
txt(s,'Target: 256 sandboxes; sequential creation.',.83,3.47,5.41,.33,15.5,'text')
txt(s,'2 envd readiness timeouts after restore\n1 Firecracker API socket refusal',.83,4.08,5.34,.82,17,'text')
txt(s,'Investigate: restore → envd readiness.',.83,5.42,5.38,.35,15.2,'blue',True)
txt(s,'SDK COMMANDS AT 64 CONCURRENCY',7.08,2.27,5.40,.27,12,'blue',True)
txt(s,'18/192 first calls failed',7.08,2.81,5.40,.55,25,'orange',True)
txt(s,'First measured call per worker · 9.38%',7.08,3.47,5.36,.34,15.5,'text')
txt(s,'Later calls: 1 failure / 171,318 requests.\n18 of all 19 errors were at the start.',7.08,4.08,5.35,.83,17,'text')
txt(s,'Investigate: client → gateway → envd.',7.08,5.42,5.36,.35,15.2,'blue',True)
takeaway(s,'Startup reliability needs attention alongside steady-state throughput.')
notes(s,
      'Pool preparation and SDK first-command errors are separate experiments, not a combined failure rate. '
      'TargetN256:sequential preparation,5 attempts,2 reached measurement,3 failed. C8 is the subsequent command-test concurrency, not create-worker count. One failure was initial Firecracker API '
      'Unix socket connection refused during PUT/metrics;2 were envd initialization timeouts after snapshot restore, '
      'with about10s readiness budget. This is not a measured hard capacity limit. The independent<=1create/s slow ramp '
      'also failed on request117 after116 successes; it remains in source evidence but is omitted from the slide for focus. '
      'CPU bursts accompanied some envd failures, without proving a unique contention mechanism. '
      'SDK command cohort:single-process exec_true,64 residents and64 workers,3 runs. First measurement call per worker '
      '18/192 failed; later1/171318; overall19/171510. First measured does not mean first-ever SDK operation:setup and '
      'slot0 warmup happened earlier. All-slot command warmup atC8 did not eliminate C64 failures. Thus no simple warmup '
      'fix is claimed. Gateway/client-proxy/envd correlation and connection traces are proposed diagnostics; the full '
      'command route includes client-proxy, abbreviated in the visible investigation label. '
      'Observed failure classes:18ConnectError,1TimeoutException. The title uses failure points rather than declaring '
      'a proven syscall, CPU, memory or network root cause.',
      ['phase1-service/results/setup-failure-log-20260920.json','phase1-service/IDLE-POOL-RESULTS.md','phase2-sdk/MAIN-RESULTS.md','phase2-sdk/SUPPLEMENTAL-RESULTS.md'])

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

path=OUT/'CPU_Sandbox_Benchmark_Focused_EN.pptx';PRS.save(path)
(OUT/'chart_data.json').write_text(json.dumps(DATA,indent=2)+'\n')
(OUT/'layout-check.json').write_text(json.dumps({'slides':len(PRS.slides),'text_blocks':AUDIT},indent=2)+'\n')
(OUT/'slides_text.txt').write_text('\n\n'.join('SLIDE '+str(i+1)+'\n'+'\n'.join(sh.text for sh in sl.shapes if sh.has_text_frame) for i,sl in enumerate(PRS.slides))+'\n')
(OUT/'sources.json').write_text(json.dumps({'generated_utc':datetime.now(timezone.utc).isoformat(),'source_root':str(BASE),'source_files':[{'path':p,'sha256':hashlib.sha256((BASE/p).read_bytes()).hexdigest()} for p in SOURCE_FILES]},indent=2)+'\n')
print(path)

#!/usr/bin/env python3
"""Create a two-slide editable PPTX and a one-page executive report.

All numbers are selected from the existing frozen experiment reports.
No benchmark, model call, or service change is made by this script.
"""
from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN, MSO_AUTO_SIZE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_TICK_MARK
from pptx.chart.data import CategoryChartData
from pptx.oxml.xmlchemy import OxmlElement
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

OUT = Path(__file__).resolve().parent
BASE = OUT.parent
FONTS = Path('/data2/yuhanya/e2b-benchmark/presentation-tools/fonts')
FONT = 'Microsoft YaHei'
COL = {'bg':'F5F7FB', 'navy':'14243A', 'blue':'2368D1', 'teal':'008776',
       'orange':'BA5A20', 'text':'23374D', 'muted':'586B80', 'line':'DCE4EE',
       'white':'FFFFFF', 'pale_blue':'EAF1FC', 'pale_teal':'E5F4EF',
       'pale_orange':'FCF0E5', 'light_bar':'A8BDD9'}

pdfmetrics.registerFont(TTFont('NotoSC', str(FONTS/'NotoSansSC-Regular.ttf')))
pdfmetrics.registerFont(TTFont('NotoSC-Bold', str(FONTS/'NotoSansSC-Bold.ttf')))
pdfmetrics.registerFontFamily('NotoSC', normal='NotoSC', bold='NotoSC-Bold',
                              italic='NotoSC', boldItalic='NotoSC-Bold')

def rgb(name):
    return RGBColor.from_string(COL.get(name, name))

def rect(slide, x, y, w, h, fill='white', line=None, radius=False):
    shape=slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
                                Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid(); shape.fill.fore_color.rgb=rgb(fill)
    if line:
        shape.line.color.rgb=rgb(line);shape.line.width=Pt(.8)
    else:shape.line.fill.background()
    if radius:
        shape.adjustments[0]=.08
    effects=OxmlElement('a:effectLst');shape._element.spPr.append(effects)
    return shape

TEXT_AUDIT=[]
def text(slide, value, x, y, w, h, size=16, color='text', bold=False,
         align=PP_ALIGN.LEFT, name=None):
    shape=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h))
    if name:shape.name=name
    tf=shape.text_frame;tf.clear();tf.word_wrap=True;tf.auto_size=MSO_AUTO_SIZE.NONE
    tf.margin_left=tf.margin_right=tf.margin_top=tf.margin_bottom=0
    tf.vertical_anchor=MSO_ANCHOR.TOP
    for i,line in enumerate(value.split('\n')):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph()
        p.alignment=align;p.space_before=p.space_after=Pt(0)
        p.line_spacing=Pt(size*1.2)
        r=p.add_run();r.text=line;r.font.name=FONT;r.font.size=Pt(size)
        r.font.bold=bold;r.font.color.rgb=rgb(color)
        rp=r._r.get_or_add_rPr();rp.set('lang','zh-CN')
        ea=OxmlElement('a:ea');ea.set('typeface',FONT);rp.append(ea)
        width=pdfmetrics.stringWidth(line,'NotoSC-Bold' if bold else 'NotoSC',size)
        TEXT_AUDIT.append({'text':line,'width_pt':width,'box_width_pt':w*72,
                           'overflow':width>w*72,'font_size':size})
    return shape

def base_slide(prs, index, kicker, title, subtitle):
    s=prs.slides.add_slide(prs.slide_layouts[6]);s.background.fill.solid()
    s.background.fill.fore_color.rgb=rgb('bg')
    rect(s,.55,.42,.07,.23,'blue')
    text(s,kicker,.75,.38,10.6,.30,11,'blue',True)
    text(s,'2026.09.20',11.40,.38,1.38,.30,10.5,'muted',align=PP_ALIGN.RIGHT)
    text(s,title,.55,.87,12.25,.65,29,'navy',True)
    text(s,subtitle,.57,1.56,12.05,.38,15,'muted')
    rect(s,.55,7.04,12.23,.012,'line')
    text(s,'E2B Sandbox · 阶段 0–2 基线实验',.57,7.13,6.8,.23,9.5,'muted')
    text(s,f'{index:02d} / 02',11.8,7.11,.96,.25,10,'muted',align=PP_ALIGN.RIGHT)
    return s

def bar_chart(slide):
    data=CategoryChartData();data.categories=['单进程','4 个进程']
    data.add_series('成功吞吐（ops/s）',[312.90,1946.95])
    obj=slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(.91),Inches(3.82),
                               Inches(5.63),Inches(1.60),data)
    obj.name='Editable throughput chart'
    chart=obj.chart;chart.has_legend=False;chart.has_title=False
    chart.chart_style=10
    chart.category_axis.reverse_order=True
    chart.category_axis.major_tick_mark=XL_TICK_MARK.NONE
    chart.category_axis.minor_tick_mark=XL_TICK_MARK.NONE
    chart.category_axis.format.line.fill.background()
    chart.category_axis.tick_labels.font.name=FONT
    chart.category_axis.tick_labels.font.size=Pt(14)
    chart.category_axis.tick_labels.font.color.rgb=rgb('text')
    chart.value_axis.minimum_scale=0;chart.value_axis.maximum_scale=2500
    chart.value_axis.visible=False;chart.value_axis.has_major_gridlines=False
    plot=chart.plots[0];plot.gap_width=90;plot.has_data_labels=False
    for point,c in zip(chart.series[0].points,['light_bar','blue']):
        point.format.fill.solid();point.format.fill.fore_color.rgb=rgb(c)
        point.format.line.fill.background()
    # Also bind East Asian chart text to the selected presentation font.
    for rp in chart._chartSpace.xpath('.//a:defRPr'):
        ea=OxmlElement('a:ea');ea.set('typeface',FONT);rp.append(ea)
    # Native chart data remain editable; fixed labels avoid renderer-dependent
    # line breaking of the four-digit value outside the plot area.
    text(slide,'313',5.61,4.08,.86,.49,21,'navy',True,align=PP_ALIGN.RIGHT)
    text(slide,'1,947',5.61,4.84,.86,.49,21,'navy',True,align=PP_ALIGN.RIGHT)

def make_ppt():
    prs=Presentation();prs.slide_width=Inches(13.333333);prs.slide_height=Inches(7.5)
    prs.core_properties.title='E2B Sandbox 基线实验简报'
    prs.core_properties.subject='阶段0–2：关键发现与优化优先级'
    prs.core_properties.author='Benchmark study'
    prs.core_properties.keywords='E2B, Sandbox, Agentic RL, Benchmark'
    s=base_slide(prs,1,'MEASUREMENT / FIRST RESULT',
        'E2B 基线：量准吞吐，看首次可用',
        '面向 Agentic RL 的隔离执行服务：建立服务协议与 SDK 基线')
    rect(s,.55,2.06,12.23,.56,'white','line',True)
    text(s,'192 vCPU · 约 1.5 TiB VM',.82,2.20,4.00,.28,13.5,'navy',True)
    text(s,'2 vCPU / 4 GiB sandbox · 普通 4 KiB 页',4.66,2.20,5.95,.28,13,'muted')
    text(s,'约 229 万条计时记录',10.04,2.20,2.49,.28,12.5,'blue',True,align=PP_ALIGN.RIGHT)

    rect(s,.55,2.88,6.20,3.87,'white','line',True)
    text(s,'压测器会限制观测吞吐',.86,3.14,5.55,.40,20,'navy',True)
    text(s,'协议客户端 · 总并发=64，实例数=64',.88,3.60,5.45,.30,12,'muted')
    bar_chart(s)
    text(s,'6.22×',.91,5.57,2.02,.62,29,'blue',True)
    text(s,'观测吞吐提升',3.00,5.78,3.35,.32,16,'navy',True)
    text(s,'三轮成功吞吐中位数，单位 ops/s。\n进程、连接池与预热组织共同变化；反映测量端影响。',
         .91,6.20,5.55,.42,10.5,'muted')

    rect(s,6.97,2.88,5.81,3.87,'white','line',True)
    text(s,'首次结果晚于创建返回',7.29,3.14,5.10,.40,20,'navy',True)
    text(s,'SDK 创建返回',7.31,3.86,2.36,.32,14,'muted')
    text(s,'首个代码结果',10.00,3.86,2.38,.32,14,'muted')
    text(s,'0.27',7.29,4.23,1.92,.88,43,'navy',True)
    text(s,'秒',9.21,4.62,.40,.38,16,'muted')
    text(s,'2.16',9.98,4.23,1.92,.88,43,'teal',True)
    text(s,'秒',11.94,4.62,.40,.38,16,'muted')
    text(s,'上述两项从创建开始计时，P50',7.31,5.15,4.99,.30,12,'muted')
    rect(s,7.29,5.65,5.17,.49,'pale_teal',radius=True)
    text(s,'首次代码调用 1.03 秒 → 第二次 68 毫秒',7.48,5.77,4.80,.29,13.5,'teal',True)
    text(s,'60 个新实例；固定序列包含健康检查。\n本地精简 Code Interpreter 模板。',7.31,6.24,4.95,.42,10.5,'muted')
    s.notes_slide.notes_text_frame.text=(
        '讲解要点：本轮为阶段0–2的固定输入基线，未运行真实RL。\n'
        '左图是固定global C=N=64的协议客户端，3轮成功吞吐中位数：312.90和1946.95 ops/s。'
        '进程、连接池及预热共同变化，不能把6.22倍解释成服务端优化收益；四进程仍有5次请求失败。\n'
        '右图来自另一个实验：60个新实例，本地精简Code Interpreter模板。创建P50=269.60ms；'
        '从创建到第一次代码结果的配对P50=2155.13ms，固定序列包含中间curl健康检查，不是相加阶段P50。'
        '首次run_code P50=1026.74ms，第二次=68.39ms；不能全部归因为SDK或网络开销。\n'
        '来源：../REPORT.md；../phase1-service/MULTIGENERATOR-RESULTS.md；'
        '../phase2-sdk/APPLICATION-RESULTS.md。')

    s=base_slide(prs,2,'RELIABILITY / NEXT STEPS',
        '优先解决首次请求与批量启动可靠性',
        '优化顺序：启动就绪 → 首次连接 → 真实 RL 验证')
    rect(s,.55,2.08,6.09,2.83,'white','line',True)
    rect(s,.86,2.36,2.20,.34,'pale_blue',radius=True)
    text(s,'高并发首次调用',1.01,2.405,1.95,.24,11,'blue',True)
    text(s,'18 / 192',.87,2.93,3.47,.77,38,'navy',True)
    text(s,'9.375%',4.20,3.13,1.94,.50,23,'orange',True)
    text(s,'每 worker 首条计时调用失败',.91,3.81,5.38,.36,17,'navy')
    text(s,'整体仅 0.01108%：19 / 171,510',.91,4.27,5.34,.31,13,'muted')
    text(s,'SDK exec_true · C=N=64 · 3轮；setup 调用可更早发生。',
         .91,4.68,5.38,.22,9.3,'muted')

    rect(s,6.87,2.08,5.91,2.83,'white','line',True)
    rect(s,7.18,2.36,1.61,.34,'pale_orange',radius=True)
    text(s,'批量启动',7.34,2.405,1.29,.24,11,'orange',True)
    text(s,'2 / 5',7.19,2.93,2.84,.77,38,'navy',True)
    text(s,'进入正式测量',9.97,3.17,2.40,.38,16,'navy',True)
    for i in range(5):rect(s,7.21+i*.40,3.83,.29,.12,'teal' if i<2 else 'orange',radius=True)
    text(s,'N256 建池：3 次准备失败',9.32,3.72,3.06,.35,14,'orange',True)
    text(s,'1 次 FC 管理 socket 连接拒绝\n2 次 envd 初始化超时',7.21,4.15,5.16,.50,13,'text')
    text(s,'5 次建池尝试；不能据此确定硬容量上限。',7.21,4.68,5.06,.22,9.8,'muted')

    text(s,'接下来做什么',.58,5.20,11.4,.33,15,'navy',True)
    steps=[(.55,'01','启动就绪','补齐 FC / envd 启动证据\n再评估超时与重试策略'),
           (4.72,'02','首次连接','串联请求、连接重置\n与传输池事件'),
           (8.89,'03','验证 RL 收益','真实工具回放与 RL profile\n验证轨迹供给和 learner 等待')]
    for x,num,title,body in steps:
        rect(s,x,5.68,3.89,.99,'white','line',True)
        text(s,num,x+.18,5.85,.47,.38,18,'blue',True)
        text(s,title,x+.78,5.83,2.90,.36,17,'navy',True)
        text(s,body,x+.78,6.25,2.92,.39,10.4,'muted')
    text(s,'边界：20–30秒短窗口；主要配置3轮，N256仅2轮有效。本轮尚未证明真实RL训练加速。',
         .59,6.83,12.06,.23,9.8,'muted')
    s.notes_slide.notes_text_frame.text=(
        '讲解要点：可靠性优先于继续放大并发。\n'
        '首条计时调用18/192=9.375%，只针对SDK exec_true C=N=64三轮，不是总体错误率或第一次SDK操作。'
        '主矩阵warmup只覆盖slot0。逐sandbox预热后仍有reset，根因未确定。\n'
        'N256/C8共5次建池尝试，2次进入正式测量，3次准备失败；不是5个实例中只有2个成功。'
        '一次FC初始metrics socket连接被拒绝，两次envd初始化超时，后两次伴CPU突发。'
        '独立每秒最多一次创建的诊断也在116个成功实例后失败，不是容量上限116。\n'
        '后续先追踪启动就绪和首次连接，再用真实工具回放/RL profile验证业务收益。'
        '资源跨机UTC有约1秒不确定性；客户端单调时钟的延迟/吞吐不受影响。\n'
        '来源：../phase2-sdk/MAIN-RESULTS.md；../phase1-service/MAIN-RESULTS.md；'
        '../phase1-service/results/setup-failure-log-20260920.md；../phase1-service/IDLE-POOL-RESULTS.md。')

    bad=[x for x in TEXT_AUDIT if x['overflow']]
    if bad:raise ValueError('Text width overflow: '+str(bad))
    prs.save(OUT/'E2B_Benchmark_Briefing.pptx')
    (OUT/'layout-check.json').write_text(json.dumps({'slide_count':2,'text_boxes_checked':len(TEXT_AUDIT),
        'metric_font':'Noto Sans SC, PDF rendering substitute for Microsoft YaHei',
        'text_width_overflows':bad},ensure_ascii=False,indent=2)+'\n')

REPORT_MD='''# E2B Sandbox 基线实验简报

**面向 Agentic RL｜阶段 0–2｜2026-09-20**

E2B为Agent工具提供隔离执行环境。本轮已建立环境、服务协议与公开SDK的可复现基线。重点发现是：压测器本身会限制观测吞吐；创建返回不等于首次应用结果；高并发首次调用和批量启动存在需要优先排查的可靠性问题。

实验条件：192 vCPU、约1.5 TiB外层VM；sandbox为2 vCPU/4 GiB、普通4 KiB页。主矩阵104个有效单元，另有42个补充单元和6组多进程对照，约229万条计时记录。

| 重点 | 实测证据 | 解读与行动 |
|---|---|---|
| 量准吞吐 | 固定C=N=64，协议客户端单进程→四进程：313→1,947 ops/s（6.22×）。 | 进程、连接池和预热共同变化；先验证发生器能力，再判断服务容量。不能称为服务端优化收益。 |
| 看首次应用可用 | 60个新实例：创建P50 0.27秒；创建至首个代码结果P50 2.16秒，固定序列含健康检查。首次run_code 1.03秒，第二次68毫秒。 | 创建、首次结果与热调用分别衡量；2.16秒来自配对时间线，不是阶段P50相加。 |
| 首次请求可靠性 | SDK C=N=64三轮：每worker首条计时调用18/192失败（9.375%）；整体19/171,510（0.01108%）。 | 错误集中在测量开始，整体比例会被后续成功请求稀释。需关联首次连接与重置事件。 |
| 批量启动可靠性 | N256建池5次：2次进入测量，3次准备失败；独立慢速诊断仍在第117次创建时失败。 | 优先排查FC socket/API就绪与envd初始化超时。尚未确定硬容量上限或唯一根因。 |

**优化优先级：** 启动就绪与超时路径 → 首次连接与传输池事件 → 真实工具回放和RL联合profile，最终验证有效轨迹供给、batch尾部和learner等待。

**适用范围：** 固定输入、20–30秒短窗口，主要配置3轮；N256只有2轮有效测量。Code Interpreter为本地精简模板。跨机时钟约1秒的不确定性限制资源精确归因，客户端单调时钟延迟/吞吐仍有效。本轮未验证长稳容量或真实RL训练加速。

来源：[完整报告](../REPORT.md)、[协议多进程对照](../phase1-service/MULTIGENERATOR-RESULTS.md)、[SDK主矩阵](../phase2-sdk/MAIN-RESULTS.md)、[应用场景](../phase2-sdk/APPLICATION-RESULTS.md)。
'''

def make_report_pdf():
    styles={
        'title':ParagraphStyle('title',fontName='NotoSC-Bold',fontSize=22,leading=31,textColor=colors.HexColor('#14243A'),spaceAfter=7),
        'sub':ParagraphStyle('sub',fontName='NotoSC',fontSize=10,leading=15,textColor=colors.HexColor('#2368D1'),spaceAfter=15),
        'intro':ParagraphStyle('intro',fontName='NotoSC',fontSize=11.2,leading=18,textColor=colors.HexColor('#23374D'),spaceAfter=11),
        'small':ParagraphStyle('small',fontName='NotoSC',fontSize=9.3,leading=15,textColor=colors.HexColor('#586B80'),spaceAfter=11),
        'cell':ParagraphStyle('cell',fontName='NotoSC',fontSize=10.5,leading=17,textColor=colors.HexColor('#23374D')),
        'head':ParagraphStyle('head',fontName='NotoSC-Bold',fontSize=10,leading=16,textColor=colors.white),
    }
    doc=SimpleDocTemplate(str(OUT/'E2B_Benchmark_Executive_Report.pdf'),pagesize=A4,
        rightMargin=39,leftMargin=39,topMargin=34,bottomMargin=35,
        title='E2B Sandbox 基线实验简报',author='Benchmark study')
    story=[Paragraph('E2B Sandbox 基线实验简报',styles['title']),
      Paragraph('面向 Agentic RL · 阶段 0–2 · 2026-09-20',styles['sub']),
      Paragraph('E2B为Agent工具提供隔离执行环境。本轮已建立服务协议与公开SDK基线。重点发现是：<b>压测器会限制观测吞吐，创建返回不等于首次应用结果，高并发首次调用和批量启动存在可靠性问题。</b>',styles['intro']),
      Paragraph('实验条件：192 vCPU / 约1.5 TiB VM；sandbox 2 vCPU / 4 GiB、普通4 KiB页。主矩阵104个有效单元，另有42个补充单元、6组多进程对照，约229万条计时记录。',styles['small'])]
    raw=[['重点','实测证据','解读与行动'],
       ['量准吞吐','固定C=N=64，协议客户端：<br/>单进程 → 四进程<br/><b>313 → 1,947 ops/s（6.22×）</b>','进程、连接池与预热共同变化。先验证压测器能力，再判断服务容量。6.22×对应测量端变化。'],
       ['首次应用可用','60个新实例，P50：<br/>创建 <b>0.27秒</b><br/>创建至首个代码结果 <b>2.16秒</b><br/>首次代码1.03秒；第二次68毫秒。','创建、首次结果和热调用分别衡量。2.16秒为含健康检查的固定序列配对值，并非各阶段中位数之和。'],
       ['首次请求可靠性','SDK C=N=64，3轮：<br/>每worker首条计时调用：<br/><b>18/192（9.375%）</b> 失败<br/>整体 19/171,510（0.01108%）','失败集中在测量开始。整体比例会被后续成功请求稀释，应关联首次连接和重置事件。'],
       ['批量启动可靠性','N256的5次建池：<br/><b>2次进入测量；3次准备失败</b><br/>另一次慢速诊断在第117次创建失败。','优先排查FC socket/API就绪与envd初始化超时。尚未确定硬容量上限或唯一根因。']]
    data=[[Paragraph(v,styles['head'] if i==0 else styles['cell'])for v in row]for i,row in enumerate(raw)]
    table=Table(data,colWidths=[93,200,A4[0]-78-93-200],hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#14243A')),
         ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),9),
         ('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),9),
         ('BOTTOMPADDING',(0,0),(-1,-1),10),
         ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#F5F7FB'),colors.white]),
         ('LINEBELOW',(0,0),(-1,-1),.5,colors.HexColor('#DCE4EE'))]))
    story += [table,Spacer(1,15),Paragraph('<b>优化优先级</b>　启动就绪与超时路径 → 首次连接与传输池事件 → 真实工具回放和 RL profile，最终验证有效轨迹供给、batch尾部和learner等待。',styles['intro']),
      Paragraph('<b>范围与边界：</b>固定输入、20–30秒短窗口，主要配置3轮；N256只有2轮有效测量。Code Interpreter为本地精简模板。跨机时钟约1秒的不确定性限制资源精确归因，客户端单调时钟延迟/吞吐仍有效。本轮未验证长稳容量或真实RL训练加速。',styles['small'])]
    def footer(c,doc):
        c.setStrokeColor(colors.HexColor('#DCE4EE'));c.line(39,33,A4[0]-39,33)
        c.setFont('NotoSC',8);c.setFillColor(colors.HexColor('#586B80'))
        c.drawString(39,20,'来源：benchmark/REPORT.md 及各阶段 MAIN / APPLICATION / MULTIGENERATOR 结果')
        c.drawRightString(A4[0]-39,20,str(doc.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)

def main():
    make_ppt()
    (OUT/'E2B_Benchmark_Executive_Report.md').write_text(REPORT_MD)
    make_report_pdf()
    sources=['REPORT.md','phase1-service/MULTIGENERATOR-RESULTS.md',
             'phase1-service/MAIN-RESULTS.md','phase2-sdk/MAIN-RESULTS.md',
             'phase2-sdk/APPLICATION-RESULTS.md','phase1-service/IDLE-POOL-RESULTS.md']
    (OUT/'sources.json').write_text(json.dumps({'generated_utc':datetime.now(timezone.utc).isoformat(),
       'scope':'Selected executive highlights; no new experiments.',
       'sources':[{'path':'../'+p,'sha256':hashlib.sha256((BASE/p).read_bytes()).hexdigest()}for p in sources]},
       ensure_ascii=False,indent=2)+'\n')
    print('Created two-slide PPTX, Markdown report and report PDF.')

if __name__=='__main__':main()

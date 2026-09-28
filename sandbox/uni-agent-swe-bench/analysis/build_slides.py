"""Two visual summary slides, native editable PPTX + PDF, using local libraries only."""
import datetime as dt
import json
from pathlib import Path
from xml.sax.saxutils import escape
import zipfile
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle, FancyBboxPatch
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'slides'
W,H = 960,540
C = dict(ink='15243B', muted='65738A', blue='287CC1', pale='BFD7EC', purple='7858B2',
         green='159D84', red='D76042', orange='E5A12B', line='DAE2EC', bg='F5F8FC', white='FFFFFF')


class Scene:
    def __init__(self): self.items=[]
    def rect(self,x,y,w,h,color,radius=0): self.items.append(dict(kind='rect',x=x,y=y,w=w,h=h,color=color,radius=radius))
    def line(self,x,y,x2,y2,color,width=1): self.items.append(dict(kind='line',x=x,y=y,x2=x2,y2=y2,color=color,width=width))
    def text(self,text,x,y,w,size=14,color=None,bold=False,align='left',h=None):
        self.items.append(dict(kind='text',text=str(text),x=x,y=y,w=w,h=h or size*1.4,size=size,color=color or C['ink'],bold=bold,align=align))


def header(s,title,subtitle,page):
    s.rect(0,0,W,H,C['white'])
    s.rect(40,30,5,28,C['blue'],2)
    s.text(title,56,27,865,27,bold=True)
    s.text(subtitle,56,68,865,12,C['muted'])
    s.text(f'{page:02d} / 02',866,514,52,9,C['muted'],align='right')


def build_scenes():
    ep=pd.read_csv(ROOT/'data/episodes.csv')
    req=pd.read_csv(ROOT/'data/model_requests.csv.gz')
    tool=pd.read_csv(ROOT/'data/tool_actions.csv.gz')
    rpc=pd.read_csv(ROOT/'data/sandbox_rpc.csv.gz')
    summary=json.loads((ROOT/'data/summary.json').read_text())
    facts=json.loads((ROOT/'data/additional-facts.json').read_text())
    m=summary['step1_metrics'];b=summary['batches']['1']
    group=ep[(ep.batch==1)&(ep.instance_id=='agronholm__anyio-639')].sort_values('rollout_index')
    r=group[group.rollout_index==2].iloc[0]
    step=m['timing_s/step'];v=r.verifier_s;roll=m['timing_s/gen']-v;training=step-m['timing_s/gen']
    s=Scene()
    header(s,'长尾拖慢整批：一个 step 用了 35.32 分钟','STEP 1  ·  16 道题 × 8 samples = 128 rollout  ·  8 × MI350X',1)
    # Mutually exclusive critical-path accounting, with overlapped grading retained in rollout.
    s.rect(40,98,880,137,C['bg'],7)
    cards=[(56,'ROLLOUT 主阶段',roll/step, f'{roll/60:.2f} min',C['blue']),
           (492,'VERIFIER 收尾',v/step,f'{v:.2f} s',C['green']),
           (716,'TRAINING + 同步',training/step,f'{training/60:.2f} min',C['purple'])]
    for x,label,fraction,time,color in cards:
        s.text(label,x,109,230,11,C['muted'],bold=True)
        s.text(f'{fraction*100:.2f}%',x,129,162,30,color,bold=True)
        s.text(time,x,164,170,12,color)
    x=56;barw=848
    for value,color in [(roll,C['blue']),(v,C['green']),(training,C['purple'])]:
        width=barw*value/step;s.rect(x,181,width,20,color,0);x+=width
    s.text('E2E 关键路径近似拆分：重叠 verifier 计入 rollout，仅将末端评测单列。',56,211,840,10,C['muted'])

    s.text('整批长尾',44,254,220,16,bold=True)
    s.text('同题 8 次尝试：AnyIO #639',496,254,420,16,bold=True)
    s.line(472,260,472,475,C['line'])
    s.text('90% 完成后，仍要等',44,288,330,17)
    s.text('9.44 min',44,316,210,38,C['red'],bold=True)
    s.text('占 rollout 的 32%',252,333,210,17,C['red'],bold=True)
    lx,ly,lw=44,382,394
    cut=b['completion_quantiles_s']['0.9']/b['observed_wall_s']
    s.rect(lx,ly,lw*cut,25,C['blue']);s.rect(lx+lw*cut,ly,lw*(1-cut),25,C['red'])
    s.text('116 / 128 已完成',lx,414,195,12,C['muted'])
    s.text('剩余 12 条',lx+lw*cut,414,lw*(1-cut),12,C['red'],align='right')
    s.text('最后 1 条独占收尾',44,451,220,14)
    s.text('+5.53 min',268,447,171,22,C['red'],bold=True,align='right')
    # Every sample, with the fastest and actual batch critical sample highlighted.
    bx,by,bw=532,286,270
    for j,row in enumerate(group.itertuples()):
        yy=by+j*22.5
        color=C['red'] if row.rollout_index==2 else C['green'] if row.rollout_index==7 else C['pale']
        s.text(f'S{row.rollout_index}',498,yy-1,28,11,C['muted'])
        length=bw*row.episode_s/group.episode_s.max()
        s.rect(bx,yy,length,13,color,2)
        s.text(f'{row.episode_s/60:.2f}',bx+length+8,yy-2,70,12,color if row.rollout_index in [2,7] else C['muted'],bold=row.rollout_index in [2,7])
    s.text('min',868,282,36,10,C['muted'])
    s.text('8.75 → 28.84 min',500,477,242,20,bold=True)
    s.text('3.29×',790,474,118,27,C['red'],bold=True,align='right')
    s.text('范围：完成的第 1 步；第 2 步因 GPU 故障中断。',44,515,770,9,C['muted'])

    t=Scene()
    header(t,'一个长尾 sample：模型交互占 85%，启动耗时 112 秒','同一案例：AnyIO #639 · Sample 2 · 28.84 min · 110 轮 · 43.2K 生成 tokens',2)
    # Full measured execution path, including actual alternating model/tool spans.
    start=float(r.task_start);total=float(r.episode_s);gx,gy,gw=44,138,872
    t.text('启动 112 s',44,104,210,16,C['purple'],bold=True)
    t.text('模型 24.53 min',288,104,248,16,C['blue'],bold=True)
    t.text('工具 2.13 min',555,104,216,16,C['orange'],bold=True)
    t.text('评分 15.73 s',772,104,146,16,C['green'],bold=True,align='right')
    t.rect(gx,gy,gw,28,C['line'])
    def span(a,z,color):
        if z>a: t.rect(gx+(a-start)/total*gw,gy,(z-a)/total*gw,28,color)
    span(start,r.agent_start,C['purple'])
    for q in req[req.session_id==r.session_id].itertuples():span(q.start,q.end,C['blue'])
    for a in tool[tool.session_id==r.session_id].itertuples():span(a.start,a.end,C['orange'])
    span(r.verifier_start,r.verifier_end,C['green'])
    t.text('0',gx,173,30,10,C['muted']);t.text('28.84 min',825,173,92,10,C['muted'],align='right')
    t.line(478,208,478,480,C['line'])

    t.text('启动放大',44,207,380,17,bold=True)
    vals=[('排队 + create',r.create_wait_and_api_s),('就绪 + tmux',r.post_create_gate_hold_lower_s),('Git / shell',r.agent_start-r.tools_provisioned_at)]
    positions=[44,190,336]
    for (label,value),xx in zip(vals,positions):
        t.rect(xx,249,127,75,C['bg'],6)
        t.text(f'{value:.2f} s',xx+4,258,119,24,C['purple'],bold=True,align='center')
        t.text(label,xx+3,293,121,12,C['muted'],align='center')
    for xx in [177,323]: t.text('›',xx,269,16,23,C['muted'],align='center')
    t.line(47,337,314,337,C['purple'],1.5);t.line(47,331,47,338,C['purple'],1.5);t.line(314,331,314,338,C['purple'],1.5)
    t.text('create gate = 3：就绪后才释放名额',44,349,414,13,C['purple'],bold=True)
    t.text('整批创建爬坡',44,394,198,13,C['muted'])
    t.text('5.98 min',44,420,214,31,C['ink'],bold=True)
    t.text('返回后工作占用名额容量',268,394,196,12,C['muted'])
    t.text('≥91%',268,420,195,31,C['purple'],bold=True)
    t.text('启动细分非等比例；另有 0.78 s 入口开销。',44,465,413,10,C['muted'])

    # Real sequence of the first logical shell action: native XML bars remain editable.
    first=tool[(tool.session_id==r.session_id)&(tool.tool=='shell')].iloc[0]
    rows=rpc[(rpc.session_id==r.session_id)&(rpc.end>=first.start)&(rpc.start<=first.end)].sort_values('start')
    assert len(rows)==5 and rows.event.tolist()==['file_write','exec','exec','exec','exec']
    t.text('1 次 shell ≈ 2 s → 5 次 RPC',501,207,421,17,bold=True)
    t.text('同一 sample 的首个 shell 调用',501,232,410,11,C['muted'])
    origin=rows.start.min();rx=682;rw=209;scale=rw/1.55
    labels=['write 命令','tmux 发送','cat 退出码','cat stdout','cat stderr']
    for i,(record,label) in enumerate(zip(rows.itertuples(),labels)):
        yy=267+i*32
        color=C['green'] if i==0 else C['orange']
        t.text(label,502,yy-3,125,12,C['ink'])
        t.text(f'{record.duration_s*1000:.0f} ms',617,yy-2,60,11,C['muted'],align='right')
        t.line(rx,yy+5,rx+rw,yy+5,C['line'],.55)
        t.rect(rx+(record.start-origin)*scale,yy,max(.9,record.duration_s*scale),12,color,0)
    for sec in [0,.5,1,1.5]:
        xx=rx+sec*scale
        t.line(xx,430,xx,434,C['muted'],.7)
        t.text(f'{sec:g}',xx-13,440,27,10,C['muted'],align='center')
    t.text('秒',895,439,25,10,C['muted'])
    t.text('全量平均',502,476,101,13,C['muted'])
    t.text('4.49 RPC / 工具动作',600,470,317,21,C['orange'],bold=True,align='right')
    t.text('启动含客户端排队；RPC 时序为实测，命令总时长为秒级日志；未测得服务端 boot 分段。',44,515,804,9,C['muted'])
    evidence={'scope':'Step 1 completed; batch 1; 16 prompts x 8 samples',
              'wall_accounting':{'step_s':step,'rollout_main_including_overlapped_verifiers_s':roll,
                                 'critical_final_verifier_s':v,'training_sync_other_s':training,
                                 'method':'Critical-path approximation. Earlier graders overlap ongoing agent episodes and remain within rollout. The only separately attributed grading is the final sample verifier. Training includes logprob, actor update, advantages, weight sync and <0.1s remainder.'},
              'batch_tail':{'after90_s':b['tail_after_90pct_s'],'last_sample_alone_s':b['last_sample_penalty_s']},
              'case_session_id':r.session_id,'case_episode_s':total,'case_model_s':r.model_roundtrip_s,
              'case_startup_s':r.agent_start-r.task_start,'case_creation_wait_api_s':r.create_wait_and_api_s,
              'case_post_create_gate_hold_s':r.post_create_gate_hold_lower_s,
              'case_post_gate_setup_s':r.agent_start-r.tools_provisioned_at,
              'shell_rpc':json.loads(rows.to_json(orient='records')),
              'source_files':['data/summary.json','data/additional-facts.json','data/episodes.csv','data/model_requests.csv.gz','data/tool_actions.csv.gz','data/sandbox_rpc.csv.gz'],
              'rendering':'PPTX uses editable native text and shapes. PDF/PNG use the same scene coordinates and local fonts, not a PowerPoint screenshot.'}
    return [s,t],evidence


def render(scenes, stem='Uni-Agent_实验总结_2页', image_prefix='slide'):
    plt.rcParams['font.family']=['DejaVu Sans','Droid Sans Fallback']
    plt.rcParams['pdf.fonttype']=42
    with PdfPages(OUT/(stem+'.pdf')) as pdf:
        for i,scene in enumerate(scenes,1):
            fig=plt.figure(figsize=(W/72,H/72));ax=fig.add_axes([0,0,1,1]);ax.set(xlim=(0,W),ylim=(H,0));ax.axis('off')
            for obj in scene.items:
                color='#'+obj['color']
                if obj['kind']=='rect':
                    if obj['radius']:
                        patch=FancyBboxPatch((obj['x'],obj['y']),obj['w'],obj['h'],boxstyle=f"round,pad=0,rounding_size={obj['radius']}",linewidth=0,facecolor=color)
                    else: patch=Rectangle((obj['x'],obj['y']),obj['w'],obj['h'],linewidth=0,facecolor=color)
                    ax.add_patch(patch)
                elif obj['kind']=='line':
                    ax.plot([obj['x'],obj['x2']],[obj['y'],obj['y2']],color=color,lw=obj['width'],solid_capstyle='butt')
                else:
                    x=obj['x']+({'left':0,'center':.5,'right':1}[obj['align']])*obj['w']
                    ax.text(x,obj['y'],obj['text'],fontsize=obj['size'],color=color,fontweight='bold' if obj['bold'] else 'normal',ha=obj['align'],va='top',linespacing=1.0)
            pdf.savefig(fig)
            fig.savefig(OUT/f'{image_prefix}-{i}.png',dpi=150)
            plt.close(fig)


NS='xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
GROUP='<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
def emu(x):return str(round(x*12700))
def fill(color):return f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
def xfrm(x,y,w,h):return f'<a:xfrm><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{emu(w)}" cy="{emu(h)}"/></a:xfrm>'


def shape(obj,i,language='zh-CN'):
    typ=obj['kind'];color=obj['color']
    nv=f'<p:nvSpPr><p:cNvPr id="{i}" name="{typ} {i}"/><p:cNvSpPr'+(' txBox="1"' if typ=='text' else '')+'/><p:nvPr/></p:nvSpPr>'
    if typ=='line':
        x=min(obj['x'],obj['x2']);y=min(obj['y'],obj['y2']);w=abs(obj['x2']-obj['x']);h=abs(obj['y2']-obj['y'])
        return f'<p:sp>{nv}<p:spPr>{xfrm(x,y,w,h)}<a:prstGeom prst="line"><a:avLst/></a:prstGeom><a:ln w="{emu(obj["width"])}">{fill(color)}<a:prstDash val="solid"/></a:ln></p:spPr></p:sp>'
    base=xfrm(obj['x'],obj['y'],obj['w'],obj['h'])
    geom='roundRect' if typ=='rect' and obj['radius'] else 'rect'
    props=f'<p:spPr>{base}<a:prstGeom prst="{geom}"><a:avLst/></a:prstGeom>{fill(color) if typ=="rect" else "<a:noFill/>"}<a:ln><a:noFill/></a:ln></p:spPr>'
    body=''
    if typ=='text':
        size=round(obj['size']*100);bold=' b="1"' if obj['bold'] else '';align={'left':'l','center':'ctr','right':'r'}[obj['align']]
        pr=f'<a:rPr lang="{language}" sz="{size}"{bold}>{fill(color)}<a:latin typeface="Aptos"/><a:ea typeface="Microsoft YaHei"/><a:cs typeface="Arial"/></a:rPr>'
        paragraphs=''.join(f'<a:p><a:pPr algn="{align}"><a:lnSpc><a:spcPct val="100000"/></a:lnSpc></a:pPr><a:r>{pr}<a:t>{escape(line)}</a:t></a:r><a:endParaRPr lang="{language}" sz="{size}"/></a:p>' for line in obj['text'].split('\n'))
        body=f'<p:txBody><a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t"><a:noAutofit/></a:bodyPr><a:lstStyle/>{paragraphs}</p:txBody>'
    return f'<p:sp>{nv}{props}{body}</p:sp>'


def rels(entries):
    return '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="{id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" Target="{target}"/>' for id,kind,target in entries)+'</Relationships>'


def pptx(scenes, stem='Uni-Agent_实验总结_2页', language='zh-CN'):
    files={}
    files['_rels/.rels']=rels([('rId1','officeDocument','ppt/presentation.xml')])
    files['ppt/presentation.xml']=f'<p:presentation {NS}><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>'+''.join(f'<p:sldId id="{255+i}" r:id="rId{i+1}"/>' for i in range(1,len(scenes)+1))+f'</p:sldIdLst><p:sldSz cx="{emu(W)}" cy="{emu(H)}" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
    files['ppt/_rels/presentation.xml.rels']=rels([('rId1','slideMaster','slideMasters/slideMaster1.xml')]+[(f'rId{i+1}','slide',f'slides/slide{i}.xml') for i in range(1,len(scenes)+1)])
    clr='<p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/>'
    files['ppt/slideMasters/slideMaster1.xml']=f'<p:sldMaster {NS}><p:cSld><p:spTree>{GROUP}</p:spTree></p:cSld>{clr}<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>'
    files['ppt/slideMasters/_rels/slideMaster1.xml.rels']=rels([('rId1','slideLayout','../slideLayouts/slideLayout1.xml'),('rId2','theme','../theme/theme1.xml')])
    files['ppt/slideLayouts/slideLayout1.xml']=f'<p:sldLayout {NS} type="blank" preserve="1"><p:cSld name="Blank"><p:spTree>{GROUP}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    files['ppt/slideLayouts/_rels/slideLayout1.xml.rels']=rels([('rId1','slideMaster','../slideMasters/slideMaster1.xml')])
    colors={'dk1':C['ink'],'lt1':C['white'],'dk2':C['muted'],'lt2':C['bg'],'accent1':C['blue'],'accent2':C['purple'],'accent3':C['green'],'accent4':C['orange'],'accent5':C['red'],'accent6':C['pale'],'hlink':C['blue'],'folHlink':C['purple']}
    scheme='<a:clrScheme name="Uni-Agent">'+''.join(f'<a:{k}><a:srgbClr val="{v}"/></a:{k}>' for k,v in colors.items())+'</a:clrScheme>'
    fonts='<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Aptos Display"/><a:ea typeface="Microsoft YaHei"/><a:cs typeface="Arial"/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/><a:ea typeface="Microsoft YaHei"/><a:cs typeface="Arial"/></a:minorFont></a:fontScheme>'
    fs='<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    fmt='<a:fmtScheme name="Office"><a:fillStyleLst>'+fs*3+'</a:fillStyleLst><a:lnStyleLst>'+''.join(f'<a:ln w="{w}" cap="flat" cmpd="sng" algn="ctr">{fs}<a:prstDash val="solid"/><a:miter lim="800000"/></a:ln>' for w in [6350,12700,19050])+'</a:lnStyleLst><a:effectStyleLst>'+('<a:effectStyle><a:effectLst/></a:effectStyle>'*3)+'</a:effectStyleLst><a:bgFillStyleLst>'+fs*3+'</a:bgFillStyleLst></a:fmtScheme>'
    files['ppt/theme/theme1.xml']=f'<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Uni-Agent"><a:themeElements>{scheme}{fonts}{fmt}</a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>'
    for i,scene in enumerate(scenes,1):
        files[f'ppt/slides/slide{i}.xml']=f'<p:sld {NS}><p:cSld><p:spTree>{GROUP}'+''.join(shape(o,j+2,language) for j,o in enumerate(scene.items))+'</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
        files[f'ppt/slides/_rels/slide{i}.xml.rels']=rels([('rId1','slideLayout','../slideLayouts/slideLayout1.xml')])
    overrides={'ppt/presentation.xml':'presentation.main','ppt/slideMasters/slideMaster1.xml':'slideMaster','ppt/slideLayouts/slideLayout1.xml':'slideLayout'}
    overrides.update({f'ppt/slides/slide{i}.xml':'slide' for i in range(1,len(scenes)+1)})
    files['[Content_Types].xml']='<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'+''.join(f'<Override PartName="/{p}" ContentType="application/vnd.openxmlformats-officedocument.presentationml.{typ}+xml"/>' for p,typ in overrides.items())+'<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>'
    for text in files.values(): ET.fromstring(text)
    filename=OUT/(stem+'.pptx')
    with zipfile.ZipFile(filename,'w',zipfile.ZIP_DEFLATED) as z:
        for name,xml in files.items():z.writestr(name,'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'+xml)
    return filename


if __name__=='__main__':
    OUT.mkdir(exist_ok=True)
    scenes,evidence=build_scenes()
    render(scenes)
    target=pptx(scenes)
    (OUT/'slide-data.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False)+'\n')
    (OUT/'scene.json').write_text(json.dumps([s.items for s in scenes],ensure_ascii=False)+'\n')
    print(json.dumps({'slides':len(scenes),'pptx':str(target),'native_shapes':[len(s.items) for s in scenes]},ensure_ascii=False))

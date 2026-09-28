# 实验简报与两页PPT

- [一页简报 PDF](E2B_Benchmark_Executive_Report.pdf)
- [可编辑简报 Markdown](E2B_Benchmark_Executive_Report.md)
- [两页 PowerPoint](E2B_Benchmark_Briefing.pptx)
- [PPT 实际渲染预览 PDF](E2B_Benchmark_Briefing.pdf)
- [第一页预览](slide-1.png) / [第二页预览](slide-2.png)

第一页概括压测器对吞吐的影响、创建返回与首次应用结果的区别；第二页概括首次计时调用和批量启动的可靠性问题，以及下一步优化顺序。PPT备注中有简短讲解提示和来源。

PowerPoint使用原生文本和柱状图，均可编辑；柱状图的数值标注是独立文本框，修改数据时应同步更新。字体指定为Microsoft YaHei；PDF使用Noto Sans SC嵌入字体，便于在不同机器直接阅读。

内容来自已有实验报告，`sources.json`保存来源SHA。本次仅整理汇报材料，没有重跑实验。原实验交付的`SOURCE-MANIFEST.json`保留不动，本目录单独记录`manifest.json`。

## 生成与校验

生成脚本为`generate_briefing.py`。Python依赖见`requirements.txt`；字体和便携LibreOffice放在`/data2/yuhanya/e2b-benchmark/presentation-tools/`，没有系统安装。

```bash
/data2/yuhanya/e2b-benchmark/presentation-env/bin/python generate_briefing.py
FONTCONFIG_FILE=/data2/yuhanya/e2b-benchmark/presentation-tools/fonts.conf \
  /data2/yuhanya/e2b-benchmark/presentation-tools/soffice-portable \
  --convert-to pdf --outdir . E2B_Benchmark_Briefing.pptx
```

已核对简报为1页、PPT为2页，并实际渲染检查中文、数字和布局。`validation.json`记录页数和可编辑图表结构，`layout-check.json`记录生成时的文字宽度检查。

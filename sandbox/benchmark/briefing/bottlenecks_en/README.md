# Sandbox bottlenecks — revised English briefing

This version is organized around bottleneck mechanisms and concrete agent-workload examples.

- [Editable two-slide PowerPoint](Agentic_RL_Sandbox_Bottlenecks_EN.pptx)
- [PowerPoint PDF preview](Agentic_RL_Sandbox_Bottlenecks_EN.pdf)
- [One-page English report](Sandbox_Bottlenecks_Report_EN.pdf)
- [Editable Markdown report](Sandbox_Bottlenecks_Report_EN.md)

Slide 1 covers cold start/reset, process creation/system calls, and filesystem I/O.
Slide 2 covers CPU quotas/throttling, memory pressure, resource contention, and compatibility.
Each category includes a mechanism, a concrete example and profiling targets.
Measured observations are labeled separately; the deck does not claim that all seven categories were confirmed in this study.

All slide content is editable text and shapes. The PDFs were rendered and visually checked.
The PowerPoint specifies Aptos; the local PDF renderer uses DejaVu Sans as its fallback.
Speaker notes contain the deployment-specific technical distinctions and source references.

## Regeneration

`generate_bottlenecks.py` only reads existing reports and creates presentation files. It does not call the sandbox service.

```bash
/data2/yuhanya/e2b-benchmark/presentation-env/bin/python generate_bottlenecks.py
FONTCONFIG_FILE=/data2/yuhanya/e2b-benchmark/presentation-tools/fonts.conf \
  /data2/yuhanya/e2b-benchmark/presentation-tools/soffice-portable \
  --convert-to pdf --outdir . Agentic_RL_Sandbox_Bottlenecks_EN.pptx
```

The Python dependencies are the same as `../requirements.txt`. `sources.json` records the source report hashes; `manifest.json` records the generated artifacts.

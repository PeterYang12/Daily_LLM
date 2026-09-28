# CPU sandbox benchmark: data-driven English briefing

This revision uses the completed Phase 0/1/2 experiments. It replaces the presentation narrative with measured observations, their implications, and the next tests. It does not run new benchmarks or change the service. Previous presentation versions remain in their original directories.

- [Editable PowerPoint — 6 slides](CPU_Sandbox_Benchmark_Data_EN.pptx)
- [Presentation PDF](CPU_Sandbox_Benchmark_Data_EN.pdf)
- [All slides at a glance](overview.png)
- [Companion report — 2 pages](Benchmark_Findings_EN.pdf) / [Markdown](Benchmark_Findings_EN.md)

## Slide sequence

1. **Measurement controls:** fixed global C=N64, one vs four load-generator processes; Phase 0 logical-file/HTTP-entity calibration. The multi-process cross-check comes from Phase 1/2.
2. **Service creation:** cycle throughput and create P95 vs concurrency, plus target-256 pool preparation outcomes.
3. **Public SDK:** create, health command, first/second code latency; separate C64 first-measurement error cohort.
4. **Bottleneck cases 2/6/1:** first-use overhead, resource contention, and CPU throttling; evidence status and next experiment.
5. **Bottleneck cases 3/4/5/7:** syscall/process costs, filesystem, memory, and compatibility; what is measured and what still needs testing.
6. **CPU sandbox infrastructure:** host/VM/guest configuration, gateway routing, one E2B Pod, and Firecracker microVMs.

Charts are native PowerPoint charts with embedded data. Text, boxes and architecture arrows are editable. Detailed definitions, scope, failure accounting and source references are in the slide speaker notes. The chart medians and P50s have different aggregation rules, stated on slides and in notes. The seven case numbers match the original list.

## Local rebuild

Dependencies are in `../requirements.txt` (python-pptx, ReportLab, PyMuPDF and fonttools; Pillow is also installed for previews). The existing presentation Python environment is used below.

```bash
cd /home/yuhanya/Daily_LLM/sandbox/benchmark/briefing/data_driven_en
/data2/yuhanya/e2b-benchmark/presentation-env/bin/python generate_deck.py
/data2/yuhanya/e2b-benchmark/presentation-env/bin/python render_report.py
FONTCONFIG_FILE=/data2/yuhanya/e2b-benchmark/presentation-tools/fonts.conf \
  /data2/yuhanya/e2b-benchmark/presentation-tools/soffice-portable \
  --convert-to pdf --outdir . CPU_Sandbox_Benchmark_Data_EN.pptx
/data2/yuhanya/e2b-benchmark/presentation-env/bin/python finalize.py
```

The PDF is exported from the actual PPTX with LibreOffice 24.2.7.2. Aptos is mapped to DejaVu Sans by the local renderer; Office may render font spacing slightly differently. The generator checks text geometry before saving, and all six exported slides were visually inspected.

## Evidence and reproducibility

- `chart_data.json`: the local source summaries used for the charts.
- `sources.json`: source paths and SHA256 hashes for this revision.
- `layout-check.json`: explicit slide text geometry checks.
- `validation.json`: final page, editable-chart, language, source and bounds checks.
- `manifest.json`: hashes of deliverables, scripts, source summary, and previews.

Throughput comparisons are descriptive short-window observations, not isolated CPU effects or certified capacity. First-result timing includes the health command. Startup failures are preserved; CPU pressure is associated with some failures but not established as a unique cause. Clean reset and real RL performance were not measured. Full experiment configuration, logs and analysis remain in the phase directories.

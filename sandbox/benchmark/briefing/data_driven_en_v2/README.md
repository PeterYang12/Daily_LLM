# Focused benchmark presentation — English revision 2

[Editable PowerPoint](CPU_Sandbox_Benchmark_Focused_EN.pptx) · [PDF](CPU_Sandbox_Benchmark_Focused_EN.pdf) · [Overview](overview.png)

This six-slide revision keeps the key findings from completed experiments and the actual failure points. It removes the file-compression side story and the generic seven-category bottleneck checklist. The previous version remains in `../data_driven_en/`.

## Contents

1. **The benchmark client limited the command test.** At the same 64 sandboxes and 64 requests in flight, one versus four client processes delivered 313 versus 1,947 successful commands/s. This is a load-generator setup limitation, not a service optimization.
2. **Higher creation concurrency increased latency.** Create P95 rose from 0.54 to 2.91 seconds at worker counts 3→8; create-delete cycle throughput did not improve.
3. **First Python call versus repeat, with configuration.** Exact Hello World code, local minimal template, Python 3.11.6, 2 vCPU / 4 GiB, ordinary 4 KiB pages, one sandbox/call at a time, 3×20 fresh sandboxes, SDK versions and operation sequence.
4. **Three observed performance limits.** Client setup, creation waiting, and first-use cost, each paired with an optimization direction to test.
5. **Actual startup and command failures.** Three failed pool preparations and first-measurement SDK errors. Failure stages are identified; unique underlying causes remain unresolved.
6. **Infrastructure.** Host, QEMU/KVM outer VM, K3s gateway/E2B Pod, Firecracker microVMs and basic resource configuration.

Pool preparation was sequential. Its C8 label refers to the later command measurement, not eight concurrent create workers. First measured requests may follow setup/warmup and are not necessarily the first-ever SDK operation. These distinctions are recorded in the speaker notes.

## Rebuild locally

```bash
cd /home/yuhanya/Daily_LLM/sandbox/benchmark/briefing/data_driven_en_v2
/data2/yuhanya/e2b-benchmark/presentation-env/bin/python generate_deck.py
FONTCONFIG_FILE=/data2/yuhanya/e2b-benchmark/presentation-tools/fonts.conf \
  /data2/yuhanya/e2b-benchmark/presentation-tools/soffice-portable \
  --convert-to pdf --outdir . CPU_Sandbox_Benchmark_Focused_EN.pptx
/data2/yuhanya/e2b-benchmark/presentation-env/bin/python finalize.py
```

The deck contains four native editable charts; text, diagram boxes, and arrows are editable. The PDF was exported from the PPTX through LibreOffice 24.2.7.2 using the existing Aptos→DejaVu Sans font mapping. Source paths, hashes, exact chart data, speaker notes, geometry checks, and final validation accompany the deliverables.

No new experiment or service change was performed. This revision does not claim a validated optimization for the creation or first-use paths, a clean-reset benchmark, or real-RL training speedup.

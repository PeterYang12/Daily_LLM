# Resource observations

Host and VM measurements are separate. Host totals include other activity; they are not costs attributable solely to E2B.

Rate means are weighted by each layer's actual monotonic sample interval. Gauges report sampled min/max/median. Disk await is weighted by completed I/O count, not time. Delta counters are summed only across observed intervals. Missing observations are not zero-filled.

Run attribution uses UTC windows and requires synchronized clocks. Only sample intervals entirely inside a run window are retained; boundary intervals are excluded, with coverage reported. No rate is split into an unobserved subinterval. Capture summaries may include warmup, setup and cleanup.

For the 2026-09-20 campaign, post-experiment checks found approximately one-second uncertainty in guest/host UTC alignment. Existing VM window summaries remain unshifted and support approximate attribution only. Client monotonic metrics and host/generator CPU use their own common clock domain. See [clock alignment and hypothetical sensitivity](/home/yuhanya/Daily_LLM/sandbox/benchmark/phase0-environment/CLOCK-ALIGNMENT.md); no historical clock correction is asserted.

See `resource_summary.csv/json`, `resource_coverage.csv/json`, and `resource_quality.json`. Cgroup CPU/throttle measurements cover continuously observed cgroups; omitted new/exited intervals do not establish zero use. NBD per-device coverage may include only active observations.

- guest / 0db3435482b507c6: [PNG](plots/resources-guest-0db3435482b507c6.png) · [SVG](plots/resources-guest-0db3435482b507c6.svg)
- host / 0db3435482b507c6: [PNG](plots/resources-host-0db3435482b507c6.png) · [SVG](plots/resources-host-0db3435482b507c6.svg)
- guest / 258f8c5c18b2ec5d: [PNG](plots/resources-guest-258f8c5c18b2ec5d.png) · [SVG](plots/resources-guest-258f8c5c18b2ec5d.svg)
- host / 258f8c5c18b2ec5d: [PNG](plots/resources-host-258f8c5c18b2ec5d.png) · [SVG](plots/resources-host-258f8c5c18b2ec5d.svg)
- guest / 941474cd946db383: [PNG](plots/resources-guest-941474cd946db383.png) · [SVG](plots/resources-guest-941474cd946db383.svg)
- host / 941474cd946db383: [PNG](plots/resources-host-941474cd946db383.png) · [SVG](plots/resources-host-941474cd946db383.svg)

Raw sampler files remain external; the full sampled metric table was not duplicated in this report.

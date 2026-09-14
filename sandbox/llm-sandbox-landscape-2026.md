# LLM Sandbox Landscape, September 2026

Surveyed 2026-09-14. Star counts pulled live from the GitHub API that day, not quoted from secondary articles.
Latency and concurrency figures mostly come from vendor blogs (Northflank, Beam, Modal, PandaStack all sell competing products) — treat them as orders of magnitude and benchmark yourself.

Related: [OpenEnv API reference](./openenv-api.md)

## Layers

"Sandbox" gets used for four distinct things. They are:

```
① Trainer          verl / SkyRL-train / TRL / prime-rl / Tinker
                   ↑ consumes token_ids + logprobs + reward
② Harness          Harbor — runs a trial: env start → agent.run → verify → teardown
                   ↑
③ Env interface    OpenEnv — Gymnasium-style step/reset/state over HTTP/WS
                   ↑
④ Sandbox runtime  E2B / Modal / Daytona / Docker / GKE / OpenSandbox
```

Sections 1–2 cover layer ④ (actual sandboxes), section 3 covers layer ①, section 4 covers ② and ③.

## 1. Hosted cloud sandboxes

| Platform | Isolation | Open source / stars | Cold start | Ease of use | RL support |
|---|---|---|---|---|---|
| **E2B** | Firecracker microVM (strongest) | SDK Apache-2.0: [E2B](https://github.com/e2b-dev/E2B) **13.8k**, [runtime](https://github.com/e2b-dev/runtime) **1.4k**; hosted cloud closed | ~80ms in-region, p50 ~200ms cross-region | ★★★★★ de facto default, most mature SDK, widest integration | Best indirect support: vLLM's [vime](https://github.com/vllm-project/vime) ships an E2B backend, most RL envs default to it. No GPU. |
| **Modal Sandbox** | gVisor (userspace kernel, weaker than microVM) | [client SDK](https://github.com/modal-labs/modal-client) only, **514** | 2–4s (CPU) | ★★★★☆ Python-native, image defined dynamically in code | **Most practical for RL**: native GPU scheduling with per-second billing, GPU memory snapshots, claims 100k+ concurrent sandboxes (demoed 1M in under a minute, 2026-07) |
| **Daytona** | container by default, Kata opt-in | [daytonaio/daytona](https://github.com/daytonaio/daytona) **71.7k** stars but **closed-source since 2026-06** — stars are a historical stock, not activity | ~90ms, fastest | ★★★★☆ | Mediocre; untrusted code must run on the Kata tier |
| **Morph Cloud** | microVM + Infinibranch | Closed, Python-only SDK | Fast | ★★★☆☆ contact sales, no public pricing | **Best for tree-search RL**: memory-level fork of a running VM in <250ms, ports and processes survive |
| **Runloop (Devbox)** | VM + container, two layers | [SDK](https://github.com/runloopai/api-client-python) **28** | Medium | ★★★★☆ built-in SWE-Bench Verified / SWE-smith | Eval more than training: **no GPU**, fork captures disk only, not live memory |
| **Northflank** | Kata / Firecracker / gVisor, selectable | Closed | 1–2s | ★★★☆☆ | Good: 100k+ concurrent, L4/A100/H100/H200, self-serve BYOC, public pricing |
| **Beam** | gVisor | Runtime open source, [beta9](https://github.com/beam-cloud/beta9) **1.8k** AGPL-3.0 | Fast | ★★★★☆ | Good: `create_from_memory_snapshot`, GPU rollouts, runs in your own AWS/GCP/Azure account |
| **Vercel Sandbox** | Firecracker microVM | [vercel/sandbox](https://github.com/vercel/sandbox) **198** Apache-2.0 | Fast | ★★★★☆ but tied to Vercel | Poor, not designed for RL |
| **Cloudflare Sandbox** | container / Workers edge | [sandbox-sdk](https://github.com/cloudflare/sandbox-sdk) **1.1k** | Very fast | ★★★★☆ | Poor: no GPU, long tasks constrained |
| **Fly Machines / Sprites** | Firecracker | Closed | p50 ~2.8s (Sprites warm restore <1s) | ★★★☆☆ | Mediocre, **no GPU** |
| **Prime Intellect Sandboxes** | Docker | [prime CLI/SDK](https://github.com/PrimeIntellect-ai/prime) **325** MIT | — | ★★★★☆ | **RL-native**: wired to the Environments Hub (2,500+ envs, 1k+ community-built) and prime-rl; the INTELLECT-3.1 recipe uses it directly. GPU sandboxes still on the roadmap. |

**Isolation ranking:** real VM with its own kernel (E2B, Vercel, Fly — Firecracker) > userspace kernel (Modal, Beam — gVisor) > shared host kernel container (Daytona default, Cloudflare). For adversarial untrusted code this ordering is a hard constraint.

## 2. Open source / self-hosted

| Project | Base | Stars | License | Ease of use | RL support |
|---|---|---|---|---|---|
| [**OpenSandbox**](https://github.com/opensandbox-group/OpenSandbox) (was alibaba/OpenSandbox) | Docker + Kubernetes runtimes | **15.2k** | Apache-2.0 | ★★★★☆ five SDK families (Py/Java/TS/.NET/Go) + MCP | Lists "RL Training" as a target scenario, but **no first-party verl or SkyRL integration** — you wire it yourself |
| [**kubernetes-sigs/agent-sandbox**](https://github.com/kubernetes-sigs/agent-sandbox) | K8s CRD, gVisor/Kata via RuntimeClass | **3.8k** | Apache-2.0 | ★★★☆☆ needs K8s fluency | Has a **warm-pool** resource specifically for cold starts; CNCF's OpenRL proposal treats it as the standard env layer. Officially not production-ready yet. |
| [**microsandbox**](https://github.com/superradcompany/microsandbox) | local microVM | **8.2k** | Apache-2.0 | ★★★★☆ local-first | Weak, single-machine oriented |
| [**agent-infra/sandbox**](https://github.com/agent-infra/sandbox) (ByteDance AgentTARS) | container with Browser/Shell/File/VSCode | **5.9k** | Apache-2.0 | ★★★★☆ batteries included | Good fit for GUI / browser agent environments |
| [**bytedance/SandboxFusion**](https://github.com/bytedance/SandboxFusion) | container | **1.1k** | Apache-2.0 | ★★★☆☆ | Built for code execution + grading; **usable for RLVR out of the box** (bundles many benchmark datasets with scoring) |
| [**vndee/llm-sandbox**](https://github.com/vndee/llm-sandbox) | thin Docker/K8s/Podman wrapper | **1.1k** | MIT | ★★★★★ lightest, pip install and a few lines | Weak — good as a tool, not for large-scale rollouts |
| [**agentscope-runtime**](https://github.com/agentscope-ai/agentscope-runtime) (Alibaba) | container | **863** | Apache-2.0 | ★★★★☆ | Agent serving, not training |

## 3. Sandbox integration on the RL framework side

| Framework | Stars | How environments/sandboxes attach |
|---|---|---|
| [**verl**](https://github.com/verl-project/verl) | **23.4k** | AgentLoop does request-level async; environments run as separate services ("Environment as a Service", HTTP/gRPC). Sandbox is your choice. |
| [**Agent Lightning**](https://github.com/microsoft/agent-lightning) (Microsoft) | **18.1k** | Makes arbitrary agent frameworks trainable; the sandbox comes with the agent |
| [**OpenPipe ART**](https://github.com/OpenPipe/ART) | **10.7k** | Multi-step agent training on real tasks, custom environments |
| [**rllm**](https://github.com/rllm-org/rllm) | **5.8k** | Environment abstraction built into the trainer |
| [**SkyRL**](https://github.com/NovaSky-AI/SkyRL) | **2.3k** | Most mature sandbox scheduling: rollout split into init (cold-start sandbox) / run / reward as a three-stage pipeline, ~1.55× speedup from the async dispatcher. One config line switches backend between verl / SkyRL-train / Tinker. |
| **prime-rl** | — | Natively bound to the Environments Hub and Prime Sandboxes |
| **NVIDIA ProRL Agent** (2026-03) | paper | Argues the above couple rollout and training too tightly; proposes the full rollout lifecycle as a standalone HTTP service. Uses **Singularity** rather than Docker on Slurm/HPC, since rootless execution is required. |

### The architectural trend: decoupling

The 2026 direction is to move environments and sandboxes **out of the trainer process into standalone HTTP services**, with backend-agnostic trajectory formats so one environment can feed verl, SkyRL, prime-rl, or Tinker.

ProRL Agent's argument is worth keeping: rollout is I/O-bound (spinning sandboxes, holding long-lived tool sessions) while training is GPU-bound. Putting both in one process creates conflicting system requirements, and migrating backends becomes expensive. Their answer is three independent worker pools — INIT / RUN / EVAL — overlapping phases across jobs so slow evaluations don't stall rollouts.

SkyRL's three-stage pipeline is the lightweight version of the same idea: one multi-turn rollout is at least three jobs — runtime init (cold-starting a container, CPU-bound and slow), the agent run, and reward computation (CPU-bound and long-tailed).

## 4. Two things commonly confused with sandboxes: OpenEnv and Harbor

### OpenEnv — the environment *interface standard*

[huggingface/OpenEnv](https://github.com/huggingface/OpenEnv), **2.6k** stars, BSD-3-Clause, formerly under `meta-pytorch`. A spec plus client library:

- Gymnasium-style `step()` / `reset()` / `state()`; the API explicitly credits Farama's Gymnasium.
- Environments package as Docker containers behind a FastAPI server; clients talk HTTP/WebSocket. Run locally or deploy as an HF Space. The `openenv` CLI handles init and deploy. MCP is first-class.
- **Reward-agnostic**: it does not dictate how rewards are computed or how the training loop works, only how environments are published, deployed, and consumed.
- The value is turning N×M into N+M: trainer authors integrate one API, environment authors implement once.
- Governance is a consortium: Meta-PyTorch, HF, Reflection, Unsloth, Modal, Prime Intellect, NVIDIA, Mercor, Fleet AI, Microsoft, RadixArk. Integrated with TRL (GRPO), torchforge, SkyRL, ART, Oumi, Unsloth, Lightning AI.
- Still marked experimental; the API will change.

Relation to sandboxes: **a sandbox is its implementation detail** (Docker by default). OpenEnv itself only defines the protocol. Full API breakdown in [openenv-api.md](./openenv-api.md).

### Harbor — the agent *eval / rollout harness*

[harbor-framework/harbor](https://github.com/harbor-framework/harbor), **5.2k** stars, Apache-2.0. From the Terminal-Bench team, released alongside Terminal-Bench 2.0 (the old repo is now [`harbor-framework/terminal-bench-1`](https://github.com/harbor-framework/terminal-bench-1), 2.6k stars).

Its core move is decoupling three things:

| Concept | Contents | Swappable with |
|---|---|---|
| **Task** | instruction + sandbox image + verifier | any benchmark |
| **Harness / Agent** | tool surface + agent loop | Claude Code, OpenHands, Codex CLI… |
| **Sandbox** | execution substrate | **docker / e2b / daytona / gke / modal** |

So Harbor isn't a sandbox — it's **the layer that schedules sandboxes**. The same task runs across thousands of parallel trials with different agents and different sandbox providers, and what comes out is RL rollouts.

Its RL integrations are more hands-on than OpenEnv's:

- **verl**: `RemoteAgentLoop` + Harbor, with token-level `token_ids` / `logprobs` tracing through a ProxyServer, parallelized across K8s.
- **SkyRL**: Harbor's Trial drives the rollout lifecycle (env start → `agent.run()` → verify → teardown); SkyRL provides the async training loop and vLLM engines. Much of the needed fault tolerance already exists in Harbor.
- **TRL**: external-agent mode only — an agent running its own model inside the container is opaque to the trainer, so no logprobs.

Ships `harbor-rewardkit` (20+ reward primitives plus TOML-defined LLM judges and agent-as-judge rubrics) and **ATIF**, the Agent Trajectory Interchange Format (RFC-0001).

### Side by side

| | OpenEnv | Harbor |
|---|---|---|
| Role | environment **interface standard** | eval / rollout **execution framework** |
| Unit of abstraction | one env (step/reset) | one trial (task × agent × sandbox) |
| Granularity | single interaction step | whole episode lifecycle |
| Who drives the agent loop | the trainer | Harbor; the agent can be an off-the-shelf CLI |
| Rewards | out of scope | built-in rewardkit |
| Typical use | general RL envs (Atari, chess, code exec) | containerized agentic tasks (SWE, terminal) |

In one line: **OpenEnv is the socket standard, Harbor is the batch runner, and the sandbox is the container actually burning power inside.**

> Name collision: `Harbor` is also the CNCF container registry [goharbor/harbor](https://github.com/goharbor/harbor), and a June 2026 robotics RL paper, [HARBOR (arXiv 2606.08610)](https://arxiv.org/html/2606.08610v1). Easy to cross-contaminate when searching.

## 5. Choosing

- **Pure agent code execution, strongest isolation, least ops** → E2B.
- **RL rollouts needing GPU inside the environment** → Modal or Northflank; Beam if you want it in your own cloud account.
- **Tree-search / multi-path RL** (e.g. SWE agent best-of-n) → Morph Cloud's memory-level fork is the only real answer.
- **Must self-host / data can't leave the network** → OpenSandbox (most complete, highest stars) or K8s agent-sandbox (most "standard", not yet stable enough).
- **RLVR code grading** → SandboxFusion works out of the box.
- **Training side** → write environments against the OpenEnv interface and attach to verl / SkyRL, rather than binding to a single trainer. For containerized agentic tasks (SWE, terminal), use Harbor directly.

### Why RL differs from ordinary agent code execution

A coding agent runs one long session. An RL training loop runs the same environment thousands of times concurrently. Any per-episode setup cost — reinstalling dependencies, re-cloning a repo — gets multiplied by the episode count.

Two rules that follow:

1. **Replace, don't reset.** In-place resets eventually leak state; restoring from a snapshot is strictly cleaner.
2. **GPU inside the environment matters only sometimes** — specifically when a reward model or GPU-accelerated simulator lives in the environment itself. Otherwise every step round-trips out.

On benchmarking: ignore the vendor's p50 for a `create` call on an empty template. Install your real dependencies, run your real task, create and destroy a hundred concurrently, and look at p99 of the whole loop.

## References

**Vendor comparisons** (all written by parties selling competing products)

- [Daytona vs E2B in 2026 — Northflank](https://northflank.com/blog/daytona-vs-e2b-ai-code-execution-sandboxes)
- [E2B vs Modal — Northflank](https://northflank.com/blog/e2b-vs-modal)
- [Running RL agents in secure sandboxes — Northflank](https://northflank.com/blog/reinforcement-learning-agents-in-secure-sandboxes)
- [Top Runloop alternatives — Northflank](https://northflank.com/blog/runloop-alternatives)
- [Best Sandboxes for RL Environments in 2026 — Modal](https://modal.com/resources/best-sandboxes-rl-environments)
- [Best Sandbox Providers for Reinforcement Learning in 2026 — Beam](https://www.beam.cloud/blog/best-sandbox-providers-reinforcement-learning-2026)
- [Best Morph Cloud Alternatives in 2026 — PandaStack](https://www.pandastack.ai/blog/best-morph-cloud-alternatives-2026/)
- [AI Agent Sandbox Infrastructure in 2026 — AgentMarketCap](https://agentmarketcap.ai/blog/2026/04/07/ai-agent-sandbox-infrastructure-e2b-modal-daytona-fly-machines-secure-code-execution)
- [The AI Agent Sandbox Wars — bex.co](https://bex.co/blog/2026/07/07/ai-agent-sandbox-wars)

**Papers and technical docs**

- [SkyRL-Agent (arXiv 2511.16108)](https://arxiv.org/html/2511.16108v1)
- [ProRL Agent: Rollout-as-a-Service (arXiv 2603.18815)](https://arxiv.org/html/2603.18815v1)
- [SkyRL + OpenEnv](https://skyrl.readthedocs.io/en/latest/examples/openenv.html)
- [OpenEnv docs](https://huggingface.co/docs/openenv/index) · [HF blog: community backing OpenEnv](https://huggingface.co/blog/openenv-agentic-rl)
- [Harbor RL training docs](https://www.harborframework.com/docs/training-workflows/rl) · [TRL's Harbor integration](https://huggingface.co/docs/trl/en/harbor)
- [Prime Intellect Environments Hub](https://www.primeintellect.ai/blog/environments)
- [CNCF OpenRL sandbox proposal](https://github.com/cncf/sandbox/issues/518)
- [When LLMs Grow Hands and Feet — Jiachen Liu](https://amberljc.github.io/blog/2025-09-05-agentic-rl-systems.html)
- [RL Coding Environments 101: Why Harbor Exists](https://x.com/adithya_s_k/article/2054961319179420035)
- [Training frontier knowledge work agents with SkyRL — Mercor](https://www.mercor.com/blog/training-frontier-knowledge-work-agents-a-397b-rl-training-guide-with-skyrl/)

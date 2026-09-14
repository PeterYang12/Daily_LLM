# Sandbox Options

*Part of the [Agentic RL Landscape](./README.md). Data as of 2026-09-14; entries marked ⚠️ are unverified vendor claims.*

Comparison of 18 sandbox options across deployment shape, isolation, statefulness, licensing, and backing organization.

---


| Solution | Deployment | Local | Isolation | Stateful | API shape | License / pricing | Backed by | Stars | Last push | Ease |
|---|---|:---:|---|:---:|---|---|---|---:|---|:---:|
| **Plain [Docker](https://www.docker.com)** | Local / bare metal | ✅ | container | Process-level | None (CLI/SDK) | OSS | Docker | — | — | ★★★★★ |
| **[SandboxFusion](https://github.com/bytedance/SandboxFusion)** | **Single-host docker** | ✅ | container | ❌ | `POST /run_code` | OSS | **ByteDance** | 1,065 | 2026-07-14 | ★★★★★ |
| **[E2B](https://github.com/e2b-dev/E2B)** | Cloud | ⚠️ self-hostable | **Firecracker** | ✅ | REST + WS, full SDK | SDK Apache-2.0 / service paid | E2B (YC) | **13,784** | 2026-09-12 | ★★★★★ |
| **[e2b-dev/infra](https://github.com/e2b-dev/infra)** | **Self-hosted (Nomad+Consul+Terraform)** | ✅ | Firecracker | ✅ | Same as E2B | OSS | E2B | 1,395 | **2026-09-14** | ★★ |
| **[AgentENV](https://github.com/kvcache-ai/AgentENV)** | **Self-hosted, distributed** | ✅ | ⚠️ | ✅ | **E2B-compatible** | OSS | **kvcache-ai** (Moonshot-adjacent) | **3,459** | 2026-09-12 | ★★★ |
| **[Modal](https://modal.com)** | Cloud | ❌ | gVisor | ✅ | Python SDK first | Paid ([client](https://github.com/modal-labs/modal-client) OSS) | Modal Labs | 514 (client) | 2026-09-11 | ★★★★★ |
| **[Daytona](https://github.com/daytonaio/daytona)** | Cloud | ❌ | container | ✅ | REST SDK | ⚠️ **closed-source since 2026-06** | Daytona | 71,715 * | 2026-07-24 | ★★★★ |
| **[microsandbox](https://github.com/microsandbox/microsandbox)** | **Local, no orchestrator** | ✅ | **libkrun microVM** | ✅ | Local SDK | OSS | Community | **8,248** | **2026-09-14** | ★★★★ |
| **[agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)** | **Kubernetes** | ✅ | gVisor / Kata | ✅ | **k8s CRD** (not REST) | OSS | **k8s-sigs / Google** | **3,832** | 2026-09-12 | ★★ |
| **[veFaaS](https://www.volcengine.com/product/vefaas)** | Cloud (China) | ❌ | Serverless | ✅ | Volcengine SDK | Paid | **Volcengine** | — | — | ★★★★ |
| **[OpenYuanRong](https://github.com/yuanrong-proj/yuanrong)** | Cloud (China) | ⚠️ | ⚠️ | ✅ | SDK | OSS (openEuler) | **Huawei** | 104 | — | ★★★ |
| **[SWE-ReX](https://github.com/SWE-agent/SWE-ReX)** | Library (multi-backend) | ✅ | Backend-dependent | ✅ | Python | OSS | **SWE-agent team (Princeton)** | 588 | 2026-09-07 | ★★★★ |
| **[llm-sandbox](https://github.com/vndee/llm-sandbox)** | Library (local) | ✅ | container | Weak | Python | OSS | Community | 1,119 | **2026-09-14** | ★★★★★ |
| **[Judge0](https://github.com/judge0/judge0)** | Service | ✅ | container | ❌ | REST | OSS | Community | 4,433 | 2026-08-17 | ★★★★ |
| **[Piston](https://github.com/engineer-man/piston)** | Service | ✅ | container | ❌ | REST | OSS | Community | 2,811 | 2026-07-31 | ★★★★ |
| *[gVisor](https://github.com/google/gvisor)* (primitive) | — | ✅ | User-space kernel | — | — | OSS | **Google** | 19,301 | 2026-09-14 | — |
| *[Kata](https://github.com/kata-containers/kata-containers)* (primitive) | — | ✅ | microVM | — | — | OSS | **OpenInfra** | 8,720 | 2026-09-11 | — |
| *[Firecracker](https://github.com/firecracker-microvm/firecracker)* (primitive) | — | ✅ | microVM | — | — | OSS | **AWS** | **36,717** | 2026-09-11 | — |

\* **Daytona's 71.7k stars are misleading.** That count was accumulated by its earlier "cloud dev environment" product. Production code went closed-source in 2026-06 and the public repo is frozen and unmaintained. **Do not evaluate it as a self-hostable open-source option** — treat it strictly as a managed service.

## How to Read This Table

1. **The first fork in the road is stateless vs. stateful.** SandboxFusion is cheap and pleasant but caps out at L1. L3 requires state.
2. **Only four genuinely active self-hostable OSS options exist:** [e2b-dev/infra](https://github.com/e2b-dev/infra) (heavy, Nomad stack), [AgentENV](https://github.com/kvcache-ai/AgentENV) (distributed, E2B-compatible), [microsandbox](https://github.com/microsandbox/microsandbox) (single-host microVM), [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) (Kubernetes).
3. **AgentENV is the underrated one.** 3.4k stars, actively developed, and **claims E2B API compatibility** — meaning E2B-targeting code migrates to self-hosted at near-zero cost. High value for China-based or air-gapped deployments.
4. **China-domestic options:** veFaaS (Volcengine; credited in uni-agent's acknowledgements) and OpenYuanRong (Huawei, openEuler ecosystem). This is a concrete advantage of uni-agent over Miles.
5. **API shapes do not agree**, which is the biggest source of friction today — see [Standards](./README.md#4-standards-three-layers-covered-one-gap).

---


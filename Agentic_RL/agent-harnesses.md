# Agent Harnesses

*Part of the [Agentic RL Landscape](./README.md). Data as of 2026-09-14; entries marked ⚠️ are unverified vendor claims.*

Comparison of 15 agent harnesses from an agentic RL perspective: black box vs. white box, trainability, and what each is actually good for.

---


| Harness | Box | Stars | Last push | Type | RL-trainable | Integrated by | Best suited for |
|---|:---:|---:|---|---|:---:|---|---|
| **[Claude Code](https://github.com/anthropics/claude-code)** | **Black** | **144,957** | 2026-09-14 | Closed-source CLI | ✅ via Gateway interception | **uni-agent**, NeMo Gym, Harbor | Highest-ceiling baseline; distillation / cold-start data. **Not for deep RL** |
| **[Codex CLI](https://github.com/openai/codex)** | Grey | **123,915** | 2026-09-14 | OSS CLI | ✅ same | Harbor | Same, OpenAI ecosystem |
| **[Gemini CLI](https://github.com/google-gemini/gemini-cli)** | Grey | **106,970** | 2026-09-14 | OSS CLI | ✅ same | — | Same, Google ecosystem |
| **[OpenHands](https://github.com/All-Hands-AI/OpenHands)** | White (heavy) | **87,817** | 2026-09-14 | Agent platform | ✅ ships its own runtime | NeMo Gym, Harbor | Tasks needing full IDE, browser, multimodal capability |
| **[Cline](https://github.com/cline/cline)** | White | 67,957 | 2026-09-14 | VSCode extension | ❌ IDE-bound | — | Human-in-the-loop. **Not for RL** |
| **[Aider](https://github.com/Aider-AI/aider)** | White | 48,944 | **2026-05-22** | CLI | Theoretically | — | Git-native workflows. **No commits in 4 months** |
| **[LangGraph](https://github.com/langchain-ai/langgraph)** | White | 41,596 | 2026-09-13 | Orchestration framework | ✅ | NeMo Gym, [verl-recipe](https://github.com/verl-project/verl-recipe), agent-lightning | General multi-agent workflows. **Not a coding agent** |
| **[smolagents](https://github.com/huggingface/smolagents)** | **White (minimal)** | 29,312 | 2026-08-25 | Lightweight library | ✅ | HF ecosystem | Fast prototyping, teaching, custom tool loops |
| **[Qwen Code](https://github.com/QwenLM/qwen-code)** | Grey | 27,830 | 2026-09-14 | OSS CLI | ✅ | — | China-controllable stack |
| **[SWE-agent](https://github.com/SWE-agent/SWE-agent)** | **White** | 20,318 | 2026-09-07 | Research agent | ✅ with [SWE-ReX](https://github.com/SWE-agent/SWE-ReX) | Academia | SWE-bench-style academic reproduction |
| **[mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent)** | **White (~100 LOC)** | **7,515** | 2026-09-07 | Minimal | ✅✅ **best fit** | **uni-agent**, NeMo Gym | **First choice for L3 RL**: controllable, reproducible, transparent context |
| **[Strands Agents](https://github.com/strands-agents/sdk-python)** | White | 7,237 | 2026-09-11 | AWS SDK | ✅ | Miles | AWS-ecosystem tool calling |
| **[Harbor](https://github.com/harbor-framework/harbor) / Terminus** | White | 5,205 | 2026-09-14 | Eval harness | ✅ ships RL/SFT rollout interfaces | **Miles**, uni-agent, NeMo Gym | **The standard for terminal tasks**; [benchmark](https://github.com/harbor-framework/terminal-bench) conversion |
| **[τ-bench](https://github.com/sierra-research/tau-bench)** | White | 1,433 | 2026-03-18 | Environment suite | ✅ | Miles | Tool calling + user simulation (L2) |
| **ReAct (in-house)** | **Fully white** | — | — | Write it yourself | ✅✅ | Every framework | **The actual workhorse for L3 RL**; fully controllable |

## What Black Box vs. White Box Actually Costs

| | White box (ReAct / mini-SWE-agent) | Black box (Claude Code) |
|---|---|---|
| Prompt editable | ✅ | ❌ |
| Context management visible | ✅ | ❌ (auto-compaction makes trajectories drift) |
| Tool set controllable | ✅ | ❌ |
| Version stability | You pin it | **Upstream updates change your results** |
| Token-level alignment | Direct | Reconstructed via Gateway; lossy risk |
| Measured RL gain (uni-agent) | **+14.6** | **+6.0** |

## How to Read This Table

1. **The three highest-starred harnesses (Claude Code 145k, Codex 124k, Gemini CLI 107k) were not designed for RL.** They enter the training loop only through Gateway-style interception, their behavior is not controllable, and results drift when upstream ships a new version.
2. **The RL community actually trains the low-star ones:** in-house ReAct and mini-SWE-agent (7.5k). The reason is practical — multi-turn RL demands token-level control, reproducible behavior, and transparent context management. **A 100-line agent is far easier to train than a 100,000-line CLI.**
3. **The right role for a black-box agent is "distillation source" and "ceiling baseline," not "training target."** Use Claude Code to generate high-quality trajectories for SFT cold-start, then run RL with a white-box agent.

---


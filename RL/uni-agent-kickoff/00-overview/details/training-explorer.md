# 离线架构交互讲解

[打开 architecture-explorer.html](../training-explorer.html)。这是单文件中文页面，HTML、CSS、JavaScript、精简数据和中文字体均已内嵌。直接用浏览器打开，无需启动服务或模型；单独复制该 HTML 后，在断网浏览器中也已实际验证可交互。

可以点击 Task、Agent、Sandbox、Gateway、TransferQueue、FSDP2、vLLM、Checkpoint，查看输入、输出、职责与边界；“上一环节/下一环节”按逻辑路径高亮相关层。每层提供固定 commit 的上游源码参考和相关说明。离线时可以阅读和操作，联网后可查看上游源码；本地适配见补丁与实验记录。

## 两份实测数据严格分开

| 页面区块 | 实际来源 | 展示内容 |
|---|---|---|
| 实测 A：计数 | `memagent_32b_pilot_restart`，global step 1 | 4 道题 × 每题 4 条轨迹 = 16 sessions；104 真实 contexts + 8 padding = 112 rows；7 次 Adam 更新 |
| 实测 B：GRPO 示例 | 首轮`memagent_32b_128`的global step 5；该轮后来在46步后宿主中断 | 四条 boxed 答案 19/20/20/19，reward 0.5/0/0/0.5，均值 0.25，中心化优势 +0.25/−0.25/−0.25/+0.25 |
| 教学模拟 | 浏览器内存中的四个可调数字 | 调 reward 观察均值和优势；始终显示“教学模拟，不改变实际实验数据” |

计数不是从全部异步 Agent 日志推断，而是验证保存的 `consumed_training_rollouts` 和四个 rank 的 Adam 计数。四个 ranks 各自 step=7，不会被写成 28 次独立更新。页面明确说明A/B不是同一批数据，也不属于新的`memagent_32b_128_durable`训练曲线。首轮的step5仍是真实历史奖励组，独立pilot也仍是完整1步工程验收；二者都不能代表128步最终训练收益。

Checkpoint面板中的HF-only明确限定旧pilot与旧首轮run。新durable协议每8步保存完整native model、Adam、scheduler/RNG、data及TQ，最终再导出HF评测；最终训练与恢复结果见[最终报告](../RESULTS.md)。此教学页面仍使用早期两份记录，不把它们当作最终 128 步结论。

真实表保持只读。模拟预设包括同分、全零、仅一条满分以及恢复原始分数；图中仅计算四条有效 session 的均值中心化，不除标准差，不模拟 PPO、KL 或真实梯度。原实验 JSON、轨迹、checkpoint 不会被修改。

## 查看方式

下载并用浏览器打开 HTML 即可，字体、样式和教学数据均内嵌，无需模型或服务。页面支持组件选择、步骤切换、padding 显示和奖励模拟；修改模拟数字不会改变真实实验记录。

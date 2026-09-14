# 大模型训练曲线与最终external图表

绘图只读取已经保存的训练/评测材料，不调用模型。PNG、SVG、PDF均嵌入来源hash，原工作区中的 sources JSON 保留输入 hash 和绘图数据（本仓库仅保留图片）；图表输出也计算SHA256。

`rl_plot_large.py`已支持`--status`。默认旧run `memagent_32b_128`仍读取`results/large-live-status.json`；新durable run默认读取`results/large-durable-live-status.json`。其他run应显式给status，输入中的run identity不匹配会拒绝。

最终 128 步图表已完成：[32B / 8B 对照](../figures/comparison)、[32B 训练诊断](../../03-memagent-32b-rl/figures/final-training) 和 [32B 稳定协议](../../03-memagent-32b-rl/figures/final-stable)。以下命令说明在已有完整实验材料的工作区如何重绘，绘图脚本及原始结果不随本文收录。

```bash
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/report/bin/python /lab/scripts/rl_plot_large.py \
  --root /lab --run memagent_32b_128_durable \
  --status /lab/results/large-durable-live-status.json --max-step 128 \
  --output-dir /lab/results/figures/final128-training-YYYYMMDD
```

`--max-step`不能超过status实际记录的最后一步；不会外推曲线。monitor和external是两套问题，训练图只画monitor。

最终external入口为`plot_final_external.py`，每次只接收一个协议的两组预登记base/final对照。主协议固定配对是base-durable→final-durable-step128、base-durable-repeat→final-durable-step128-repeat；stable固定配对是对应的stable名字。两套协议分别输出，重复不合并为128题，也不选较好的重复替代第一组。

```bash
# 宿主只读预检：缺任何完整final比较时返回2，不创建图。
python3 scripts/plot_final_external.py --protocol primary --check-only
python3 scripts/plot_final_external.py --protocol stable --check-only

# 仅在上述检查通过、完整比较JSON已经存在后，在CPU环境绘制。
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/report/bin/python /lab/scripts/plot_final_external.py \
  --root /lab --protocol primary \
  --output-dir /lab/results/figures/final128-primary-YYYYMMDD
docker exec -e HIP_VISIBLE_DEVICES= -e CUDA_VISIBLE_DEVICES= ua-lab-cpu \
  /lab/envs/report/bin/python /lab/scripts/plot_final_external.py \
  --root /lab --protocol stable \
  --output-dir /lab/results/figures/final128-stable-YYYYMMDD
```

输入JSON默认来自`comparison-durable-step128{,-repeat}/comparison.json`和`comparison-durable-stable-step128{,-repeat}/comparison.json`。`--first`、`--repeat`可指定保存位置，但phase、模型根、配对顺序仍必须是原协议，不能交叉拼接。输出目录必须不存在。

绘图前重新检查完整64题、原始result/provenance hash、CSV的64个唯一sample key及顺序、题目/gold和reward、当前冻结输入、同协议runtime与模型根。CI重新按原算法用seed20260912、20,000次**按问题重采样**计算，与保存的95% paired CI核对。它不是同模型repeat之间的收益CI，也不是多次训练的方差估计；`complete_base_audit`、`mode=repeatability`、缺题、错误模型根或混协议输入会拒绝。

图中分别显示两组base/final均值及各自paired CI，并列出LCS=1计数。LCS=1仍是固定boxed答案空白分词指标，不是另行实施的标准HotpotQA EM。单次训练的两次推理重复提供的是当前执行记录，不能据此宣称训练跨run稳定性。

16项边界控制见 final-external-plot-guard-validation.json（原始文件：`results/final-external-plot-guard-validation.json`）。这些是内存fixture与真实非final报告的拒绝检查，没有生成虚构final图。最终图片已经生成；结果解读见[最终报告](../RESULTS.md)。

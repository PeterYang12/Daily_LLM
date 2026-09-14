# TeX空格诊断 v1：批量计算前冻结的定义

此诊断用于区分最终答案的TeX空格格式与内容。规则由已经观察到的 `YG\ Entertainment` 个案引出，在批量处理base-recovery64和32B长文结果之前固定；它不是在看到任何结果之前预注册的主指标。

固定规则：

1. 完全按当前固定的官方reward处理输入：只查看最终响应的末300字符、转小写，调用官方 `last_boxed_only_string`，再调用官方 `remove_boxed`。既有 `\text{...}` 解包行为原样保留。
2. 仅在这一步得到的boxed答案中，用一次字符串 `replace("\\ ", " ")`，将**一个反斜杠紧接一个ASCII空格（U+0020）**替换成普通ASCII空格。处理所有出现位置，但不递归重写替换后的字符串。
3. Ground truths不变。使用官方 `_lcs_ratio` 和原来的多答案取最大值规则，计算独立的diagnostic LCS。无可提取答案或官方解包异常时仍计0。
4. 不处理 `\,`、`\_`、反斜杠+tab/其他Unicode空白，不改变大小写以外的原生规则，不从正文补答案，不用ground truth指导字符串改写，不做语义等价判断。

结果逐case保留：输入文件与sample_key、官方stored/recomputed LCS、官方解包答案、替换后答案、匹配替换次数、diagnostic LCS与差值，以及是否改变数值。跨文件不合并不同document长度的分母；失败或不完整评测不列作完成指标。

这个诊断不改变训练reward、官方LCS、已冻结的base/final主对照或paired bootstrap CI。它也不称作HotpotQA EM或内容准确率。将来应用于最终checkpoint时继续使用相同v1规则，单独报告格式修正影响的case，不能用诊断结果选择checkpoint或调训练参数。

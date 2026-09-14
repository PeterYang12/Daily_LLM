# Qwen3-8B：step0 与 step32 的真实监控案例

这些例子在看到第 32 步结果后选取，用于解释指标和展示变化；它们不是代表性抽样，也不用于选择检查点。最终仍固定训练和评测 step128。完整 before/after 响应、最后一次调用的输入 memory 与 episode UID 保存在同名 JSON。

整体第32步：64题451contexts，LCS 0.5088068182 → 0.5578615196；满分22 → 26。独立审计将3个 TeX 空格 case 的净贡献计为 +0.0286458，占观察净增58.40%；其余变化仍混合答案措辞、标点、选择和解码波动。

|原始 source index|简述|step0 boxed answer|step32 boxed answer|官方 LCS|
|---|---|---|---|---|
|6|实体答案变化|`james p. comer`|`eenasul fateh`|0.0000 → 1.0000|
|75|TeX 空格变化|`john\ john\ florence`|`john john florence`|0.3333 → 1.0000|
|55|地名表达变化|`marion`|`marion, south australia`|0.0000 → 1.0000|
|41|评分边界|`mumbai`|`mumbai, india`|1.0000 → 0.0000|
|4|boxed 答案范围变化|`greenwich village, new york city`|`new york city`|1.0000 → 0.6000|
|44|下降例|`nelson rockefeller`|`not enough information`|1.0000 → 0.0000|

source index 来自数据中的原始 extra_info；它与固定 dev parquet 内0–63的 monitor_row 编号不是同一个字段。

## 原始 source index 6

Who was known by his stage name Aladin and helped organizations improve their performance as a consultant?

实体答案变化：从 James P. Comer 切换到 gold Eenasul Fateh；这是这道题的输出改善，不能仅凭单题证明泛化。

Ground truth：`["Eenasul Fateh"]`。来源为固定监控集，7次模型调用（memory chunks + final answer）。

## 原始 source index 75

What American professional Hawaiian surfer born 18 October 1992 won the Rip Curl Pro Portugal?

TeX 空格变化：John\ John\ Florence 改为普通空格。两次都给出了同一个人的名字。

Ground truth：`["John John Florence"]`。来源为固定监控集，7次模型调用（memory chunks + final answer）。

## 原始 source index 55

Which Australian city founded in 1838 contains a boarding school opened by a Prime Minister of Australia and named after a school in London of the same name.

地名表达变化：Marion 补为 Marion, South Australia，官方 LCS 从 0 到 1；地名补全和标点会影响该指标。

Ground truth：`["Marion, South Australia"]`。来源为固定监控集，7次模型调用（memory chunks + final answer）。

## 原始 source index 41

Where is the company that Sachin Warrier worked for as a software engineer headquartered?

评分边界：Mumbai 补为 Mumbai, India，官方 LCS 从 1 到 0；不能据此直接认定地理知识变差。

Ground truth：`["Mumbai"]`。来源为固定监控集，7次模型调用（memory chunks + final answer）。

## 原始 source index 4

The director of the romantic comedy "Big Stone Gap" is based in what New York city?

boxed 答案范围变化：正文仍出现 Greenwich Village, New York City，但框内只写 New York City，LCS 降低。

Ground truth：`["Greenwich Village, New York City"]`。来源为固定监控集，7次模型调用（memory chunks + final answer）。

## 原始 source index 44

Alfred Balk served as the secretary of the Committee on the Employment of Minority Groups in the News Media under which United States Vice President?

下降例：Nelson Rockefeller 变为 Not enough information，官方 LCS 从 1 到 0。保留这个失败例。

Ground truth：`["Nelson Rockefeller"]`。来源为固定监控集，8次模型调用（memory chunks + final answer）。

可用于汇报的结论是：这套训练产生了可验证的参数和输出变化，评分变化需要逐题解释。当前案例不能单独证明能力提升，也不能把所有评分下降直接解读为知识遗忘。

## 同一案例的 memory 轨迹观察

source index6 的初始模型最后一次输入 memory 同时出现正确的 Eenasul Fateh 和错误的 James P. Comer→Aladin 关联，最终回答选择了 James P. Comer；step32 的最后 memory 保留 Eenasul Fateh，并不再出现 James P. Comer，最终回答与 gold 一致。这说明这个案例的中间 memory 和最终实体选择确实都发生了变化。

source index44 的初始最后 memory 保留 Nelson Rockefeller；step32 的最后 memory 没有这个名字，并最终回答 Not enough information。这是同时保留的 memory 信息丢失和最终得分下降观察。上述仅为真实轨迹的个案对照，尚未通过额外重复或干预实验证明因果与普遍性；原始最终输入在同名 JSON 中可复查。

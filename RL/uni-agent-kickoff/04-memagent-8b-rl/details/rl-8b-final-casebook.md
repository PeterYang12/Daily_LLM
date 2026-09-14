# Qwen3-8B：固定六个案例追踪到128步

这六题在step32后选出，之后保持同一组题追踪到step128；不是代表性抽样，也没有用于选择训练终点。全部数值来自同一64题原生监控集的独立审计，外部stable评测另行记录。

|source index|step0|step32|step64|step96|step128|
|---|---:|---:|---:|---:|---:|
|6|0.0000|1.0000|1.0000|1.0000|1.0000|
|75|0.3333|1.0000|1.0000|1.0000|1.0000|
|55|0.0000|1.0000|1.0000|0.0000|0.0000|
|41|1.0000|0.0000|0.0000|0.0000|0.0000|
|4|1.0000|0.6000|1.0000|0.2000|0.6000|
|44|1.0000|0.0000|1.0000|1.0000|0.0000|

source index 是原始数据extra_info编号；不是dev parquet内的monitor_row位置。

## source index 6

Who was known by his stage name Aladin and helped organizations improve their performance as a consultant?

初始boxed answer：`james p. comer`。

最终boxed answer：`eenasul fateh`。

完整最终回答和最后一次调用的memory输入保存在同名JSON；原step32 casebook保留中途正例、评分边界与失败例。

## source index 75

What American professional Hawaiian surfer born 18 October 1992 won the Rip Curl Pro Portugal?

初始boxed answer：`john\ john\ florence`。

最终boxed answer：`john john florence`。

完整最终回答和最后一次调用的memory输入保存在同名JSON；原step32 casebook保留中途正例、评分边界与失败例。

## source index 55

Which Australian city founded in 1838 contains a boarding school opened by a Prime Minister of Australia and named after a school in London of the same name.

初始boxed answer：`marion`。

最终boxed answer：`marion`。

完整最终回答和最后一次调用的memory输入保存在同名JSON；原step32 casebook保留中途正例、评分边界与失败例。

## source index 41

Where is the company that Sachin Warrier worked for as a software engineer headquartered?

初始boxed answer：`mumbai`。

最终boxed answer：`not specified in the provided information`。

完整最终回答和最后一次调用的memory输入保存在同名JSON；原step32 casebook保留中途正例、评分边界与失败例。

## source index 4

The director of the romantic comedy "Big Stone Gap" is based in what New York city?

初始boxed answer：`greenwich village, new york city`。

最终boxed answer：`new york city`。

完整最终回答和最后一次调用的memory输入保存在同名JSON；原step32 casebook保留中途正例、评分边界与失败例。

## source index 44

Alfred Balk served as the secretary of the Committee on the Employment of Minority Groups in the News Media under which United States Vice President?

初始boxed answer：`nelson rockefeller`。

最终boxed answer：`gerald ford`。

完整最终回答和最后一次调用的memory输入保存在同名JSON；原step32 casebook保留中途正例、评分边界与失败例。

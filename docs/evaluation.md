# 评测工具与样例

`fact_checker.py` 是可选的事实核查模块：拆分陈述，对照素材判断置信度，必要时检索证据并裁决。`run_eval.py` 用相同题目比较 V1 直接判断与 V2 核查流程，还可使用独立评分模型评估理由。

## 样例与格式

`eval_cases.sample.json` 包含 12 条 `dev` 用例：公共知识与素材上下文各 6 条，每组 `correct`、`incorrect`、`unverifiable` 各 2 条。`writing-workflow-example` 是文件内提供的示例素材，用于检查模型能否依据给定证据识别支持、矛盾和信息不足。

`eval_schema.py` 定义字段及标签规则；`eval_manifest.json` 记录样例版本、SHA-256、数量和分布。样例用于检查数据格式与调用流程，没有独立测试集，得分不作为模型效果基准。`source` 和 `note` 供人工复核，不发送给被测系统。

## 运行评测

不请求真实服务的回归检查见 [开发说明](development.md#自动检查)。真实评测需要先配置模型服务，再指定被测模型与独立评分模型。在 macOS / Linux 或 Bash 环境中执行：

```bash
EVAL_CASES_PATH=eval_cases.sample.json \
EVAL_SUT_MODEL=your-sut-model \
EVAL_GRADER_MODEL=your-grader-model \
uv run --locked python run_eval.py --split dev
```

上述命令运行公共知识题；在最后一行追加 `--with-material` 可运行素材上下文题。真实评测会访问模型和搜索服务，可能产生费用。结果默认写入 `local_artifacts/eval/results/`，留在本地。

自定义用例使用相同格式，通过 `EVAL_CASES_PATH` 指定文件。未显式指定时，工具优先读取本地 `local_artifacts/eval/eval_cases.full.json`，不存在则使用随项目提供的样例。

## 理解结果

标签符合率与理由评分分开统计，搜索失败、证据不足和模型输出故障分别记录。没有搜到信息不等于陈述错误。

模型设置、题目字段或引用素材发生变化，会影响结果的可比性。更新随项目提供的样例时，同步修改版本与清单中的校验值；分享结果时说明样本数、实际模型、数据指纹及失败情况。

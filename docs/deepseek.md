# DeepSeek V4 Pro 配置与验收

本页用于本地 Web 项目直连 DeepSeek 官方的 OpenAI 兼容接口。实际模型调用由运行机使用者验证；自动测试使用模拟 HTTP 响应，不代表真实服务已验收通过。

## 1. 更新新项目

先用 Ctrl+C 停止正在运行的后台，然后进入新项目 `writing-tutor` 的目录。执行 `git status --short --branch`，有未提交代码修改时先反馈，不覆盖。

确认工作区干净后执行：

```bash
git pull --ff-only origin main
uv sync --locked
cd web && npm ci && cd ..
bash verify.sh
git rev-parse HEAD
```

任一步失败就停止，反馈错误。检查本身不访问真实模型或搜索服务，安装依赖需要联网。

## 2. 配置模型

已有 `.env` 时直接编辑；没有时执行 `cp .env.example .env`。填入或修改下面这些配置，不要重复添加同名配置项：

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=在这里填写自己的DeepSeek密钥
LLM_MODEL=deepseek-v4-pro
MODEL_SLOOP=
MODEL_GALLEON=
TOKEN_PARAM=max_tokens
LLM_MIN_OUTPUT_TOKENS=4096
LLM_CONNECT_TIMEOUT_SECONDS=20
```

两个角色组覆盖项留空后，研究、策划、教学、编辑、终审都会使用 `deepseek-v4-pro`。密钥只保存在本机 `.env`，不要粘贴到聊天或提交到 Git。

`LLM_CONNECT_TIMEOUT_SECONDS=20` 只把每次建立连接的等待窗口设为 20 秒，适用于日志显示 `超时阶段=connect` 的情况；留空使用 SDK 默认值，允许 1–120 秒。它不改变读取数据的等待时间和 SDK 重试次数，也不会让模型更快生成内容。网络或代理持续不可达时仍会失败，等待可能比原先更久。

程序不覆盖 DeepSeek 的思考开关，使用服务商默认行为。`4096` 是本项目建议的初始输出预算，不是服务商要求的固定值；思考和正文会共用输出预算。默认每次生成最多尝试 3 次，遇到截断或仅返回思考字段时逐步提高预算，最高 `16000`。预算和重试可能增加等待时间与费用，实际费用以服务商为准。

## 3. 先验证最短真实流程

```bash
bash run.sh
```

打开 `http://127.0.0.1:8000`，点击“重新检查”。配置检查通过只说明填写完整，不验证密钥、余额或模型权限。

输入一个全新、简单的选题，例如“HTTP 缓存中强缓存和协商缓存有什么区别”。开始后观察：

1. 后台出现研究员搜索记录，页面随后显示素材与提纲；素材尽量包含可点击的来源。
2. 没有因工具调用缺少 `reasoning_content` 出现请求错误，也没有把这个思考字段展示为素材。
3. 确认提纲后，导师能生成正常讲解与问题。

这一步通过后，再按 [Mac 完整验收](mac-validation.md) 逐节作答、查看初稿、终审和本地作品。研究员实际检索使用本机执行的 DDGS；不是 DeepSeek 提供的内置搜索服务。搜索连接问题和模型兼容性问题需要分别判断。

失败时反馈代码版本、停在哪一步、页面提示和相关错误行；不要发送完整 `.env`、密钥、私人素材或全文。持续空输出或截断会在有限重试后报错，编辑失败时则提示并保留作者原回答。

## 4. 超时如何反馈

终端会记录研究员轮次、每次查询是否返回结果，以及模型调用的开始、完成或失败。搜索记录表示执行了查询，不表示一定找到了有效素材；一轮模型决策可能发起多个查询。

模型调用日志只新增数字设置、耗时和异常类型，不输出请求正文、思考字段、密钥、服务地址或服务商原始错误。它显示本次请求实际使用的设置，包含建连等待的配置覆盖：

- `connect`：建立连接的等待上限。
- `read`：等待接收数据的上限，不是整篇文章的总时限。
- `write`、`pool`：发送数据、等待本机可用连接的上限。
- `SDK重试上限`：SDK 对网络或部分 HTTP 错误的内部重试次数，与空输出/截断重试不同。

一条“模型调用”的耗时包括 SDK 内部可能发生的重试。失败时 `超时阶段` 会区分 `connect`、`read`、`write`、`pool`；无法从异常类型确定时显示 `unknown`，不会根据错误文案猜原因。页面也会显示对应提示。

遇到超时，请反馈最后一轮的 `[研究员]`、`[模型调用]` 日志和页面错误提示。若已确认是 `connect` 超时，在现有 `.env` 中新增或修改 `LLM_CONNECT_TIMEOUT_SECONDS=20`，重启后台后用同一选题复测；开始日志应显示 `connect=20秒`，其余等待值和重试上限与修改前一致。日志本身不改变等待或重试策略；如果仍超时，需要继续排查 Mac 网络、代理或服务端连接，而不能据此认定是模型思考过慢。

## 参考依据

适配依据为 DeepSeek 官方的 [思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[工具调用](https://api-docs.deepseek.com/guides/tool_calls/) 和 [Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/) 文档。后续服务端行为变化仍需重新验证。

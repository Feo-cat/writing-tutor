# DeepSeek V4 Pro 配置与诊断

本页说明如何使用 DeepSeek 的 OpenAI 兼容接口，以及如何排查连接和输出问题。

## 1. 安装与更新

按 [README](../README.md) 安装项目、创建 `.env` 并启动服务。更新项目也使用 README 中的步骤，保留已有配置与作品。

## 2. 配置模型

在项目根目录的 `.env` 中填写以下配置，每项保留一行：

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

两个角色组覆盖项留空时，研究、策划、教学、编辑和终审都使用 `deepseek-v4-pro`。密钥保存在本机 `.env`，修改后重启后台。

`4096` 是建议的初始输出预算，思考与正文共用预算。程序默认每次生成最多尝试 3 次，遇到截断或只有思考内容时逐步提高预算，最高 `16000`。增加预算和重试可能增加耗时与费用。

`20` 表示每次建立连接最多等待 20 秒，允许范围为 1–120 秒，留空使用 SDK 默认值。它只影响建连等待，不改变读取等待或 SDK 重试次数。

## 3. 模型与搜索

研究员通过本机执行的 DDGS 搜索资料，模型负责决定搜索问题并整理结果。模型接口和搜索服务使用各自的网络连接，排查时需要区分哪一步失败。

程序使用服务商默认的思考模式设置。持续空输出或截断会在有限重试后报错；编辑失败时会提示并保留作者原回答。页面与日志中的失败提示应结合实际输出检查。

## 4. 超时如何反馈

日志记录模型调用的耗时、异常类型、等待设置与超时阶段，可据此定位问题：

| 阶段 | 含义与处理 |
|---|---|
| `connect` | 建立连接超时；检查网络或代理，可尝试将 `LLM_CONNECT_TIMEOUT_SECONDS` 设为 `20` 后重启 |
| `read` | 等待接收数据超时；检查网络及服务商响应情况，增加建连等待无法解决读取超时 |
| `write`、`pool` | 发送数据或等待可用连接超时；保留相关错误行，结合当时的请求情况排查 |
| `unknown` | 无法从异常类型判断阶段，需结合页面提示和相关日志定位 |

一次调用的耗时包括 SDK 内部重试；读取等待上限也不等于整篇文章的总时限。反馈问题时提供代码版本、操作系统、失败步骤、最后一轮 `[研究员]` / `[模型调用]` 日志及页面提示，省略密钥和私人正文。

## 参考依据

DeepSeek 官方文档：[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[工具调用](https://api-docs.deepseek.com/guides/tool_calls/)、[Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/)。

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
```

两个角色组覆盖项留空后，研究、策划、教学、编辑、终审都会使用 `deepseek-v4-pro`。密钥只保存在本机 `.env`，不要粘贴到聊天或提交到 Git。

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

## 参考依据

适配依据为 DeepSeek 官方的 [思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[工具调用](https://api-docs.deepseek.com/guides/tool_calls/) 和 [Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/) 文档。后续服务端行为变化仍需重新验证。

# 写作助手 · writing-tutor

输入选题，研究员搜索资料、策划生成提纲，导师逐节先讲再问，编辑把你的回答整理成初稿，终审给出修改建议。保留你的表达和真实经历，终审不替你改写正文。

这是本地单用户 Web 程序，无需网关。程序与作品保存在本机；写作时会将所需素材和回答发送给模型服务，并通过 DDGS 联网搜索，可能产生费用。当前只适配 **OpenAI Chat Completions 兼容接口**，研究模型必须支持工具调用；不提供独立 CLI、MCP、论文雷达或假数据模式。

## 部署

环境要求：**Git、uv、Python 3.12、Node.js 24 LTS（含 npm）**。Python 可在下面通过 uv 安装。另需准备模型服务的 API 密钥与可用额度。

### 1. 下载项目

```bash
git clone https://github.com/Feo-cat/writing-tutor.git
cd writing-tutor
```

仓库准备期间为私密，需要 GitHub 访问权限。以下命令从项目根目录开始，按顺序执行，某步失败时先处理报错再继续。

### 2. 安装依赖并构建网页

```bash
uv python install 3.12
uv sync --locked
cd web
npm ci
npm run build
cd ..
```

Python 和前端依赖均按锁文件安装，无需手动激活虚拟环境。构建成功后会生成 `web/dist/index.html`。Windows PowerShell 若提示 `npm.ps1` 受限，将命令中的 `npm` 换成 `npm.cmd`。

### 3. 配置模型

复制配置样板，已有 `.env` 时直接编辑。

macOS / Linux：

```bash
if [ ! -e .env ]; then cp .env.example .env; fi
```

Windows PowerShell：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
```

在编辑器中打开项目根目录的 `.env`，填写以下内容。每项保留一行，密钥只放在 `.env`，不要填入公开样板或提交到 Git。

| 配置项 | 填写内容 |
|---|---|
| `LLM_BASE_URL` | 服务商文档中的 API 基础地址，是否带 `/v1` 以其文档为准；直连 OpenAI 可留空 |
| `LLM_API_KEY` | 自己在服务商 API 平台创建的密钥；也支持 `OPENAI_API_KEY` |
| `LLM_MODEL` | 服务商支持的 API 模型名，默认用于全部角色 |

API 基础地址不是聊天网站地址，也不要自行追加 `/chat/completions`。

**DeepSeek V4 Pro 示例：**在 [DeepSeek API 平台](https://platform.deepseek.com/) 创建密钥，按下面填写，替换密钥占位内容：

```dotenv
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=替换为你自己的DeepSeek_API密钥
LLM_MODEL=deepseek-v4-pro
MODEL_SLOOP=
MODEL_GALLEON=
TOKEN_PARAM=max_tokens
LLM_MIN_OUTPUT_TOKENS=4096
LLM_CONNECT_TIMEOUT_SECONDS=20
```

这份配置让全部角色共用 `deepseek-v4-pro`。`4096` 是建议的初始输出预算，`20` 是每次建立连接的等待秒数。其他服务商使用自己的地址、密钥和模型，输出预算可先留空。详见 [DeepSeek 配置说明](docs/deepseek.md) 和 [官方接入文档](https://api-docs.deepseek.com/)。

### 4. 启动

macOS / Linux：

```bash
bash run.sh
```

Windows PowerShell：

```powershell
.\run.ps1
```

保持终端运行，打开 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**，按 Ctrl+C 停止后台。

没有模型配置也能打开首页查看缺项，但不能开始写作。**修改 `.env` 后需要重启后台**，再刷新页面或点击“重新检查”；配置检查只检查填写情况，不验证密钥、额度或模型权限。

## 使用流程

1. 输入选题，点击“开始写作”，等待素材与提纲。
2. 确认提纲后点击“就按这个开始”；也可以填写意见后“按意见重排”。重排会调用策划模型，不会重新搜索。
3. 阅读导师的讲解，用自己的话回答。Enter 发送，Shift+Enter 换行。
4. 完成各节后查看初稿与终审建议，点击“下载初稿 .md”；已保存的作品可从首页档案入口查看。

默认产物目录为 `local_artifacts/blogs/`。相同选题可能复用素材、提纲与已完成章节；独立的新文章请使用有区别的选题名称。当前续写依赖已保存的文件，**刷新、关页或重启后不保证恢复尚未完成的对话**。

## 可选配置

以下均写入 `.env`，修改后重启。样板中以 `#` 开头的配置需去掉注释后才生效。

| 配置项 | 用途与默认值 |
|---|---|
| `MODEL_SLOOP` | 覆盖研究员、策划的模型；留空使用 `LLM_MODEL` |
| `MODEL_GALLEON` | 覆盖导师、编辑、终审的模型；留空使用 `LLM_MODEL` |
| `AUTHOR_PROFILE` | 可选的背景和讲解偏好，留空使用通用导师提示 |
| `TOKEN_PARAM` | 默认 `max_tokens`；服务商要求时改为 `max_completion_tokens` |
| `LLM_MIN_OUTPUT_TOKENS` | 最低输出预算，留空或 `0` 沿用各步骤默认值；不能超过 `CHAT_TOKENS_CAP`（默认 `16000`） |
| `LLM_CONNECT_TIMEOUT_SECONDS` | 建立连接的等待上限，范围 `1`～`120` 秒，留空用 SDK 默认值；不改变读取等待或重试次数 |
| `LOCAL_ARTIFACTS_DIR` | 产物根目录，默认 `local_artifacts/`，文章位于其 `blogs/` 子目录 |
| `MAKINGOF_MATERIAL` | 复盘素材的 UTF-8 文本文件路径；填写后整个服务进入项目复盘模式，以该素材代替联网检索 |
| `MAKINGOF_OUT` | 复盘稿路径，默认是产物目录下的 `blogs/draft-makingof.md` |
| `GW_LEDGER` | 可选的兼容网关账本路径；留空显示“未接入费用记录”，不影响写作 |

角色模型共用同一个 API 地址和密钥，当前不能为两组分别配置不同服务商。`MODEL_RAFT`、`MODEL_ARK` 不用于 Web 主流程，保持空白即可。

作者背景示例：

```dotenv
AUTHOR_PROFILE="熟悉 Python，希望用中文解释，多举实际项目中的例子。"
```

背景会随相关请求发送给模型。输出预算不是字数要求，思考内容也可能占用额度；增加预算或触发重试可能增加耗时与费用。费用以服务商账单为准，“未接入费用记录”不代表免费。

项目复盘示例，先准备对应的素材文件：

```dotenv
MAKINGOF_MATERIAL=local_artifacts/blogs/material-my-project.md
MAKINGOF_OUT=local_artifacts/blogs/draft-my-project.md
```

每个项目使用独立输出文件；清空 `MAKINGOF_MATERIAL` 并重启可恢复学习模式。复盘稿的终审附在文件末尾，档案不一定有独立终审或素材文件。网页档案只扫描产物目录的 `blogs/`，不会读取外部输出位置。

相对路径以项目根目录为基准，路径带空格时使用英文引号，Windows 可用 `D:/MyWriting` 写法。修改产物目录不会迁移旧文件；自定义到仓库内的新目录时，需要自行补充 `.gitignore`。

## 更新与备份

停止后台，在项目根目录执行 `git status --short --branch`，确认没有需要保留处理的代码修改后更新：

```bash
git pull --ff-only origin main
uv sync --locked
cd web
npm ci
npm run build
cd ..
```

Windows PowerShell 也可使用这些命令；npm 脚本受限时改用 `npm.cmd`。完成后重新启动后台。新版本需要增加配置时修改已有 `.env`，不要用样板覆盖；日常启动只需执行启动命令。

**`.env` 和 `local_artifacts/` 已被 Git 忽略，需要自己备份。**Git 同步代码，不会同步密钥和作品；使用外部素材或产物目录时，也要备份对应文件。

## 常见问题

| 问题 | 处理方式 |
|---|---|
| 首页打不开或提示先构建前端 | 确认后台已启动、构建成功且 `web/dist/index.html` 存在；重新构建后重启 |
| 始终显示“等待配置” | 检查根目录 `.env` 的缺项，保存并重启；已有同名进程环境变量会优先于 `.env` |
| 密钥、地址、模型或工具调用报错 | 核对服务商配置、模型权限、工具调用支持，以及 `TOKEN_PARAM` |
| 模型连接超时 | 查看日志的超时阶段；`connect` 可尝试设置建连等待 `20` 秒，持续失败需排查网络或代理；该设置不解决 `read` 超时 |
| 搜索失败或没有结果 | 单独检查搜索网络；模型接口可达不代表 DDGS 搜索也可达 |
| 模型持续空输出或截断 | 参考输出预算配置；重试耗尽会提示失败，编辑失败时会提示并保留作者原回答 |
| 作品保存失败或档案缺失 | 检查目录权限、空间及目录配置；尚未产出初稿的记录不一定进入档案 |

PowerShell 禁止执行 `run.ps1` 时，可直接启动：

```bash
uv run --locked uvicorn server:app --host 127.0.0.1 --port 8000
```

8000 端口被占用时，可将上述命令的端口改为 `8001`，并访问对应地址。更多超时诊断见 [DeepSeek 说明](docs/deepseek.md#4-超时如何反馈)。反馈错误时附操作系统、失败步骤、错误行和 `git rev-parse HEAD` 输出，不要附密钥或私人正文。

## 开发与验证状态

安装依赖后，在 macOS / Linux 项目根目录执行 `bash verify.sh`，运行 Python 检查及前端 lint/build。检查使用模拟模型或 HTTP 响应，不调用真实模型和搜索，也不能代替真实流程验收。

目前 GitHub 自动检查已通过；Mac 已验证安装、无配置首页和 DeepSeek 大纲生成，完整写作、保存、下载尚待集中验收。Windows / Linux 完整使用流程尚未实测。项目许可待发布前确认，当前未添加许可证。

进一步阅读：[Mac 验收](docs/mac-validation.md)、[目录与迁移记录](docs/migration.md)、[评测工具](docs/evaluation.md)。

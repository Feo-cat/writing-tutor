# 写作助手 · 从下载安装到第一次写作

写作助手是一个在你电脑上运行的 Web 程序：你输入选题，它搜索资料、整理提纲，再像导师一样逐节讲解和提问。你用自己的话回答，编辑把回答整理成初稿，终审给出修改建议。文章以你的回答为基础，终审不会替你改写正文。

本手册从安装工具、`git clone` 下载代码开始，带你完成模型配置、打开网页和日常更新。第一次使用，按顺序完成第 1～6 步即可；可选配置以后再看。

## 阅读路线

- [1. 准备工具和模型服务](#step-1)
- [2. 下载项目](#step-2)
- [3. 安装项目依赖并构建网页](#step-3)
- [4. 创建并填写配置文件](#step-4)
- [5. 启动网页](#step-5)
- [6. 完成第一次写作](#step-6)
- [7. 以后怎么启动、更新和备份](#step-7)
- [8. 按需要调整配置](#step-8)
- [9. 遇到问题怎么办](#troubleshooting)
- [开发说明与当前验证范围](#development)

这里的“本地运行”指程序和作品保存在自己的电脑上。真实写作仍需联网调用你配置的模型服务，学习模式还会联网搜索。选题、所需素材和你的回答会发送给模型服务，调用可能产生费用。程序默认只供本机单人使用，无需另外安装网关。

**当前版本仍在发布前验收阶段。**Mac 已验证安装、无配置首页和 DeepSeek 生成大纲；完整写作、保存和下载仍待集中验收。Windows、Linux 的完整使用流程尚未实测，下面的对应命令不代表平台验收已完成。

<a id="step-1"></a>
## 1. 准备工具和模型服务

你需要这几样东西：

| 工具或服务 | 用来做什么 | 怎么准备 |
|---|---|---|
| Git | 下载项目、获取后续更新 | 按下面的系统说明安装 |
| Node.js 和 npm | 安装网页依赖、生成浏览器使用的页面 | 推荐 Node.js 24 LTS；安装 Node.js 时会一起安装 npm |
| uv | 管理 Python 和项目的 Python 依赖 | 按下面的命令安装 |
| Python 3.12 | 运行写作后台 | 第 3 步通过 uv 安装，无需提前单独配置 |
| 模型服务的 API 密钥 | 让程序调用大模型 | 在你选用的服务商 API 平台创建，确认有可用额度和模型权限 |

先准备前三项，就能安装项目、打开配置页面。真正开始写作前才需要填入模型密钥。只有聊天网页账号还不够，需要能用于 API 调用的密钥。

### macOS

打开系统的“终端”应用。如果尚未安装 Git，执行下面的命令，并等待系统弹出的安装窗口完成：

```bash
xcode-select --install
```

如果提示命令行工具已安装，可以跳过。此安装方式见 [Git 的 macOS 安装说明](https://git-scm.com/install/mac)。

打开 [Node.js 下载页](https://nodejs.org/en/download)，选择 **24 LTS** 和 macOS 安装包，按安装向导完成安装。Apple Silicon 芯片选 ARM64，Intel 芯片选 x64。

在终端安装 uv：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

这是 [uv 官方安装命令](https://docs.astral.sh/uv/getting-started/installation/)，会下载并运行安装脚本。安装完成后关闭终端窗口，再打开一个新窗口，让新命令生效。

### Windows

从 [Git for Windows 下载页](https://git-scm.com/install/windows) 安装 Git，再从 [Node.js 下载页](https://nodejs.org/en/download) 安装 **24 LTS** 的 Windows 安装包。安装 Git 时保留允许从命令行使用 Git 的选项。

在开始菜单中打开 **PowerShell**，执行：

```powershell
winget install --id=astral-sh.uv -e
```

这是 [uv 官方提供的 WinGet 安装方式](https://docs.astral.sh/uv/getting-started/installation/#winget)。如果系统找不到 `winget`，按同一官方页面的 Windows 安装说明安装 uv。完成后关闭 PowerShell，再打开一个新窗口。

本文 Windows 命令统一写 `npm.cmd`，它是随 Node.js 安装的 npm 入口，可以避免 PowerShell 把 `npm` 解析为受执行策略限制的 `npm.ps1`。

### Linux

按 [Git 的 Linux 安装说明](https://git-scm.com/install/linux) 安装 Git，按 [Node.js 下载页](https://nodejs.org/en/download) 对应发行版的说明安装 Node.js 24 LTS。uv 使用上面的 macOS / Linux 官方安装命令。后续选择标注为“macOS / Linux”的命令执行。

### 检查工具是否就绪

macOS / Linux：

```bash
git --version
uv --version
node --version
npm --version
```

Windows PowerShell：

```powershell
git --version
uv --version
node --version
npm.cmd --version
```

每条命令都应输出版本号，Node.js 建议为 `v24.x.x`。如果出现“找不到命令”，先确认安装完成并重新打开终端，再继续。

<a id="step-2"></a>
## 2. 下载项目

先选一个存放代码的目录。下面以当前用户目录下的 `Developer` 为例，不需要把路径改成作者电脑上的路径。

macOS / Linux：

```bash
mkdir -p "$HOME/Developer"
cd "$HOME/Developer"
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force -Path "$HOME/Developer" | Out-Null
Set-Location "$HOME/Developer"
```

然后在这个目录执行下载命令，两种系统相同：

```bash
git clone https://github.com/Feo-cat/writing-tutor.git
```

**确认下载成功后**，进入新目录：

```bash
cd writing-tutor
```

`git clone` 会自动创建 `writing-tutor` 文件夹。之后说的“项目根目录”就是这个文件夹，里面应能看到 `README.md`、`server.py`、`.env.example` 和 `web` 文件夹。

若目标目录已经存在，先确认是不是之前下载的项目；已有项目按第 7 步更新。不要删除现有目录来重新安装，里面可能有你的配置和作品。

仓库准备期间仍为私密，只有获授权的 GitHub 账号可以下载。若提示 `Repository not found`，先确认访问权限和 Git 使用的登录账号。若提示 SSL、连接超时等错误，先处理网络连接，下载未完成时不要执行后续步骤。

<a id="step-3"></a>
## 3. 安装项目依赖并构建网页

下面的命令从 **项目根目录** 执行。每条命令完成后再执行下一条；遇到报错就停在当前步骤，不要继续启动。

先安装 Python 3.12，再安装后台需要的库，两种系统命令相同：

```bash
uv python install 3.12
uv sync --locked
```

uv 可以直接[安装指定 Python 版本](https://docs.astral.sh/uv/guides/install-python/)。项目已通过 `.python-version` 指定 3.12，`uv sync --locked` 会在项目内建立独立的 `.venv` 环境，并按锁文件安装依赖。你不需要手动激活这个环境。

接着安装网页依赖并构建页面。

macOS / Linux：

```bash
cd web
npm ci
npm run build
cd ..
```

Windows PowerShell：

```powershell
cd web
npm.cmd ci
npm.cmd run build
cd ..
```

`npm ci` 按项目锁定的版本安装网页依赖；`npm run build` 把网页代码生成到 `web/dist/`。最后的 `cd ..` 是返回项目根目录。

**这一步完成的标志：**命令没有报错，构建输出中出现 `built in ...`，并生成 `web/dist/index.html`。安装会联网下载依赖，但不会调用大模型。

<a id="step-4"></a>
## 4. 创建并填写配置文件

### 4.1 创建自己的 `.env`

`.env.example` 是可以公开的空白样板；`.env` 是你自己的配置文件。程序启动时读取项目根目录里的 `.env`。

macOS / Linux，在项目根目录执行：

```bash
if [ ! -e .env ]; then cp .env.example .env; fi
```

Windows PowerShell：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
```

这些命令只会在 `.env` 不存在时创建它，已有配置不会被覆盖。

打开配置文件：

- macOS：在同一终端执行 `open -e .env`，使用文本编辑打开。
- Windows：在同一 PowerShell 执行 `notepad .env`，使用记事本打开。
- Linux：用文本编辑器打开项目根目录的 `.env`；若安装了 nano，也可执行 `nano .env`。

macOS 文件管理器默认隐藏点开头的文件，可以用 `Command + Shift + .` 显示。保存时使用 UTF-8 纯文本，文件名必须是 `.env`，不要保存成 `.env.txt` 或富文本文件。

### 4.2 先认识三个主要配置

每行都是 `配置名=配置值`，只修改等号右边。同一个配置名保留一行；以 `#` 开头的是注释，不会生效。

| 配置名 | 填什么 | 去哪里找 |
|---|---|---|
| `LLM_BASE_URL` | 服务商提供的 OpenAI 兼容 API 基础地址 | 服务商 API 文档中的 `base_url` 或“接口地址” |
| `LLM_API_KEY` | 你自己的 API 密钥 | 服务商 API 平台的密钥管理页面 |
| `LLM_MODEL` | 该密钥有权调用的模型标识 | 服务商文档的模型列表；按原样复制 API 模型名 |

API 地址与聊天网站地址不同。不要填浏览器聊天页面，也不要在基础地址后自行加上 `/chat/completions`；是否需要 `/v1` 以服务商文档为准。直连 OpenAI 时，`LLM_BASE_URL` 可以留空。已有 `OPENAI_API_KEY` 环境变量也能提供密钥，但初次使用建议统一填写 `LLM_API_KEY`。

本项目适配 **OpenAI Chat Completions 兼容格式**，研究员使用的模型还必须支持 **工具调用（function calling）**。这让模型能提出搜索请求，再由本机的 DDGS 搜索库查询资料；不是模型服务自带的联网搜索。Anthropic 原生接口不在当前适配范围内。

### 4.3 完整示例：使用 DeepSeek V4 Pro

到 [DeepSeek API 平台](https://platform.deepseek.com/) 创建自己的 API 密钥，确认账号可以调用对应模型。地址和模型名称见 [DeepSeek 官方接入说明](https://api-docs.deepseek.com/)。

在 `.env` 中找到并修改下面这些行；没有的行再添加。**把密钥那一行的中文占位说明替换成真实密钥**，其他可选配置先保留样板中的空值：

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

这份配置让研究、策划、教学、编辑、终审共用 `deepseek-v4-pro`。`4096` 是本项目为思考模型建议的初始输出预算，`20` 是每次建立连接时允许等待的秒数；它们不保证固定的生成速度或整篇完成时间。更详细的含义见第 8 步及 [DeepSeek 配置说明](docs/deepseek.md)。

如果使用其他服务商，填写它自己的地址、密钥和模型，不要沿用上例中的 DeepSeek 地址。`TOKEN_PARAM` 按其文档选择 `max_tokens` 或 `max_completion_tokens`；`LLM_MIN_OUTPUT_TOKENS` 可先留空，使用各步骤默认预算。

**保存 `.env` 后，继续第 5 步。**密钥只放在自己的电脑，不要贴进截图、问题反馈或 Git 提交。项目已忽略 `.env`，公开样板 `.env.example` 中不要填写真实密钥。

<a id="step-5"></a>
## 5. 启动网页

确认终端位于项目根目录。

macOS / Linux：

```bash
bash run.sh
```

Windows PowerShell：

```powershell
.\run.ps1
```

如果 PowerShell 提示禁止运行脚本，可直接执行下面的等效启动命令，无需修改系统执行策略。macOS / Linux 也能使用这条命令：

```bash
uv run --locked uvicorn server:app --host 127.0.0.1 --port 8000
```

启动后，终端会持续显示服务日志，不会立即回到输入命令的状态，这属于正常运行。保持这个窗口打开，在浏览器访问：

**[http://127.0.0.1:8000](http://127.0.0.1:8000)**

这是你电脑上的网页地址，不是模型的 API 地址。一个后台服务就能同时提供网页和写作功能。

**这一步完成的标志：**能看到写作助手首页。配置完整时，输入选题后可以点击“开始写作”；没有填写密钥或模型时，会显示“等待配置”和具体缺项，开始按钮保持禁用。没有密钥也可以先确认网页能打开。

以后每次修改 `.env`，都需要先在运行后台的终端按 **Ctrl+C** 停止服务，再运行启动命令，然后刷新首页或点击“重新检查”。“重新检查”只查看当前进程已加载的配置，不会代替重启，也不会验证密钥、余额或模型权限；这些在真实调用时才会检查。

<a id="step-6"></a>
## 6. 完成第一次写作

第一次可以用一个范围小、容易核对的选题，例如“HTTP 缓存中强缓存和协商缓存有什么区别”。以下操作会调用真实模型，可能产生费用。

1. **输入选题，点击“开始写作”。**研究员搜索资料，策划生成提纲。等待时可以在后台终端看到搜索和模型调用进度。
2. **确认提纲。**满意时点击“就按这个开始”；需要调整时，在意见框写下想改的地方，再点击“按意见重排”。重排会调用策划模型，但不会重新搜索素材。
3. **阅读讲解，用自己的话回答。**导师会逐节讲解并提问；在输入框按 Enter 发送，Shift+Enter 换行。回答不需要像文章一样工整，编辑会负责整理。
4. **逐节完成问答。**导师可能追问，也可能进入下一节；编辑依据你的回答生成段落。
5. **查看初稿和终审建议。**点击“下载初稿 .md”，得到 Markdown 文本文件，可以用文本编辑器打开。终审建议由你决定如何采纳。
6. **以后查看作品。**重新打开首页，从作品档案入口查看已经保存的内容；默认文件放在项目的 `local_artifacts/blogs/`。

写作是多次模型调用加上你逐轮回答的过程，不会一次请求就完成整篇文章。生成速度取决于网络、模型和选题。写作中保持页面和后台开启：现有续写依靠已落盘的素材、提纲与已完成章节，**不能保证刷新、关页或重启后恢复尚未完成的对话**。

再次使用相同选题时，程序可能复用已有素材、提纲并跳过已完成的章节。想开始一篇独立的新文章，请使用有区别的选题名称；不要为了重新开始而直接删除旧作品。

<a id="step-7"></a>
## 7. 以后怎么启动、更新和备份

### 平时启动和停止

首次安装成功后，不必每次重新下载或安装依赖。打开终端，进入原来的项目根目录，再执行第 5 步的启动命令即可。

按本手册示例安装时，目录为：

```bash
cd "$HOME/Developer/writing-tutor"
```

这条目录命令在 macOS / Linux 和 PowerShell 中都可以使用。若你选了别的位置，换成实际路径。

暂时不用时，在后台终端按 Ctrl+C 停止。只关闭网页不会停止后台。更新或重启前先结束正在进行的问答，以免丢失尚未保存的状态。

### 获取后续版本

停止后台，进入项目根目录，先查看代码状态：

```bash
git status --short --branch
```

正常情况下，只有开头的分支信息，没有列出修改过的文件。若下面列出了文件，先保留并确认这些修改，再更新；不要用强制覆盖或删除文件来消除差异。被忽略的 `.env` 和 `local_artifacts/` 不会因为这个命令没有列出就消失。

确认没有需要处理的代码修改后，按顺序执行，每条成功后再执行下一条：

```bash
git pull --ff-only origin main
uv sync --locked
```

然后重新安装网页依赖、构建页面。

macOS / Linux：

```bash
cd web
npm ci
npm run build
cd ..
```

Windows PowerShell：

```powershell
cd web
npm.cmd ci
npm.cmd run build
cd ..
```

更新成功后按第 5 步启动。如果新版增加了配置项，参照更新说明修改已有 `.env`；**不要重新复制样板覆盖自己的配置**。仅更新代码而没有重新构建，浏览器可能仍然显示旧页面。

### 哪些文件需要自己保存

| 文件或目录 | 保存了什么 | 是否通过 Git 同步 |
|---|---|---|
| `.env` | 你的模型密钥、模型选择和个人设置 | 否，自己妥善保管 |
| `local_artifacts/` | 默认的素材、提纲、问答、草稿和终审记录 | 否，需要自己备份 |
| `.venv/`、`web/node_modules/`、`web/dist/` | 本机依赖和构建结果 | 否，可重新安装、构建 |
| 源码、`.env.example` 和说明文档 | 程序本身和公开配置样板 | 是，使用 Git 更新 |

Git 保存了程序代码，**不等于备份了你的文章**。把 `.env` 和 `local_artifacts/` 另存到你自己的备份位置；若配置了外部素材、输出或产物目录，也需要备份对应位置。

<a id="step-8"></a>
## 8. 按需要调整配置

这些配置都写在项目根目录的 `.env` 中，保存后重启后台生效。第一次使用，先保留默认值即可。

### 给不同角色分配模型

| 配置项 | 对应角色 | 留空时的行为 |
|---|---|---|
| `LLM_MODEL` | 所有角色的默认模型 | 需要另外填齐下面两组模型才能开始写作 |
| `MODEL_SLOOP` | 研究员、策划 | 使用 `LLM_MODEL` |
| `MODEL_GALLEON` | 导师、编辑、终审 | 使用 `LLM_MODEL` |
| `MODEL_RAFT`、`MODEL_ARK` | 保留给内部工具的档位，当前 Web 主流程不用 | 首次使用保持空白 |

想全部使用同一个模型，只填 `LLM_MODEL` 即可。想分别设置，就在两个覆盖项里填该服务商支持的 API 模型名。这里的 `sloop`、`galleon` 是程序中的分组名称，不是让你填入的模型名称。

两组角色共享同一个 `LLM_BASE_URL` 和 `LLM_API_KEY`，当前不能分别接入两家不同地址、不同密钥的服务。研究员所在组必须支持工具调用。负责推进章节的流程控制是程序代码，不额外调用一个“总指挥模型”。

### 告诉导师你的背景

可以填写一行简短背景，例如：

```dotenv
AUTHOR_PROFILE="我刚开始学习编程，希望用中文和生活中的例子解释，每次先讲一个概念。"
```

留空时使用通用的导师提示。这里的文字可能随请求发送给模型，填写与讲解有关的背景即可。它帮助调整讲解方式，不保证模型每次都完全遵守。

### 输出预算和连接等待

| 配置项 | 含义 | 怎么填 |
|---|---|---|
| `TOKEN_PARAM` | 服务商接受的输出长度参数名 | 默认 `max_tokens`；服务商要求时改为 `max_completion_tokens` |
| `LLM_MIN_OUTPUT_TOKENS` | 为 Web 写作步骤设置最低输出预算 | 留空或 `0` 使用各步骤默认值；思考模型可参考 DeepSeek 示例填 `4096` |
| `LLM_CONNECT_TIMEOUT_SECONDS` | 每次建立模型连接时等待多久 | 留空用 SDK 默认；可填 `1`～`120` 秒，连接超时时可尝试 `20` |

Token 是模型计算输入和输出长度的单位，不等于汉字数。这里的预算是请求允许模型使用的输出额度，不是要求它必须写够多少字；思考模型的思考内容也可能占用这个额度。`LLM_MIN_OUTPUT_TOKENS` 不能超过 `CHAT_TOKENS_CAP`，后者默认 `16000`，初次使用保持默认即可。

空输出、截断或无效工具调用会触发有限重试。默认每次生成最多尝试 3 次；研究员最多做 4 轮搜索决策，必要时再请求一次不带工具的总结。一轮可能包含多个查询。模型 SDK 对部分网络错误也可能重试，因此这些数字不是整篇文章的请求总数上限。提高预算或重试都可能增加等待时间与费用。

连接超时设置只影响“建立连接”，不会加速模型生成，也不改变读取响应的等待时间或 SDK 重试次数。具体排查见下方常见问题。

### 作品目录和项目复盘模式

| 配置项 | 用途与默认行为 |
|---|---|
| `LOCAL_ARTIFACTS_DIR` | 个人产物根目录。留空默认是项目中的 `local_artifacts/`，文章放在它的 `blogs/` 子目录 |
| `MAKINGOF_MATERIAL` | 你的 UTF-8 素材文本文件路径。留空为学习模式；填写后整个服务进入项目复盘模式，使用这份素材代替网上检索 |
| `MAKINGOF_OUT` | 项目复盘稿的输出路径。默认是产物目录下的 `blogs/draft-makingof.md` |
| `GW_LEDGER` | 可选的兼容网关账本文件路径。留空不接入费用记录，不影响写作 |

样板里的这些路径行以 `#` 开头。需要启用时，去掉行首的 `#`，再修改等号右边；无此行也可以自行添加。相对路径以本手册启动时的项目根目录为基准，外部位置建议填完整路径。路径有空格时用英文引号包住；Windows 路径可使用 `D:/MyWriting` 这样的正斜杠写法，避免反斜杠转义问题。

初次使用建议保持默认目录，它已被 Git 忽略。自定义到其他位置不会自动修改 Git 忽略规则；若放在仓库内的新目录，应先将该目录加入 `.gitignore`。更改产物根目录不会搬迁旧文件，档案页也只会读取新的 `blogs/` 目录。

**想复盘自己的项目时：**先把真实素材保存为 `local_artifacts/blogs/material-my-project.md` 这样的 UTF-8 纯文本文件，再在 `.env` 中设置：

```dotenv
MAKINGOF_MATERIAL=local_artifacts/blogs/material-my-project.md
MAKINGOF_OUT=local_artifacts/blogs/draft-my-project.md
```

填写的是文件路径，不是把整篇素材贴进配置。重启后，服务会围绕这份素材追问你的实际经历，仍需要模型密钥。每个项目使用自己的输出文件名，避免混入已有复盘稿；恢复学习模式时清空 `MAKINGOF_MATERIAL` 并重启。

为了从网页档案查看复盘作品，建议像上例一样把输出放在默认的 `blogs/` 目录，使用普通 `.md` 文件名。当前复盘模式把终审意见附在稿件文件末尾，档案中不一定会另有独立的“终审”文件；它也不会把素材自动复制成学习模式的同名素材文件。填写外部输出路径时，网页档案不会自动扫描那个位置。

没有配置 `GW_LEDGER` 时，页面显示“未接入费用记录”，**不表示模型调用免费**。页面只能展示已有的兼容账本记录，实际费用以服务商账单为准。

<a id="troubleshooting"></a>
## 9. 遇到问题怎么办

| 现象 | 先检查什么 |
|---|---|
| 找不到 `git`、`uv`、`node` 或 npm | 回到第 1 步，确认工具安装成功并重新打开终端 |
| `git clone` 提示无权限或找不到仓库 | 检查地址、GitHub 账号及私密仓库访问权限 |
| 下载代码或依赖时出现 SSL、连接失败 | 先确认本机能访问对应网站及网络、代理状态，失败的下载步骤需要完成后再继续 |
| npm 提示 Node 版本不支持 | 检查 `node --version`，按第 1 步安装 Node.js 24 LTS，再重新安装网页依赖 |
| 提示先构建前端，或访问首页得到 404 | 完成第 3 步的构建，确认 `web/dist/index.html` 存在，然后重启后台 |
| PowerShell 提示不能运行 `.ps1` | npm 使用本文的 `npm.cmd`；后台使用第 5 步的直接启动命令 |
| `Address already in use` / 8000 端口被占用 | 先停止自己之前启动的后台；需要保留其他服务时，用下面的 8001 端口命令 |
| 网页一直显示“等待配置” | 检查编辑的是根目录 `.env`，不是样板或 `.env.txt`；保存后重启后台，再检查页面列出的缺项 |
| 密钥被拒绝、模型或地址不存在 | 检查密钥所属服务、API 基础地址、精确模型名和模型权限；修正后重启后台 |
| 服务拒绝请求、工具调用或参数错误 | 确认是 Chat Completions 兼容接口，研究模型支持工具调用，`TOKEN_PARAM` 与服务要求一致 |
| 额度不足或请求过于频繁 | 到服务商平台查看额度和限流信息，稍后重试 |
| `超时阶段=connect` | 检查到模型服务的网络或代理连接；可以将 `LLM_CONNECT_TIMEOUT_SECONDS` 设为 `20` 后重启，再观察日志 |
| `超时阶段=read` | 已进入等待响应的阶段，检查网络与模型服务状态；增加建连等待并不会延长读取等待 |
| 模型持续返回空内容或截断内容 | 查看页面提示；思考模型参考预算配置。程序重试耗尽会报错，编辑失败时会提示并保留作者原回答 |
| 研究员搜索失败或结果为空 | 检查本机搜索网络及选题。模型接口能连接不代表 DDGS 搜索也能连接；搜索日志出现查询也不保证有有效结果 |
| 保存失败或档案找不到作品 | 检查产物目录是否可写、磁盘空间，以及是否修改过目录设置；未产出初稿的研究记录不一定出现在档案列表 |
| 刷新或重启后不能继续上一轮对话 | 当前只支持依靠已保存文件续写，未完成对话不保证恢复；保留现有作品文件 |

需要改用另一个本地端口时，在项目根目录执行：

```bash
uv run --locked uvicorn server:app --host 127.0.0.1 --port 8001
```

随后打开 [http://127.0.0.1:8001](http://127.0.0.1:8001)。

修改 `.env` 并重启后仍没有变化时，检查启动终端是否已经设置了同名环境变量；已有进程环境变量优先于 `.env`。不要把完整环境变量或 `.env` 打印出来作为反馈，以免泄露密钥。

反馈问题时，提供操作系统、失败步骤、页面提示、相关错误行，以及下面命令输出的代码版本即可：

```bash
git rev-parse HEAD
```

不要附带密钥、完整 `.env`、私人素材或文章全文。超时问题可以提供最后一组 `[研究员]`、`[模型调用]` 日志和页面提示，具体示例见 [DeepSeek 超时说明](docs/deepseek.md#4-超时如何反馈)。

<a id="development"></a>
## 开发说明与当前验证范围

当前主要维护 Web 写作流程，未提供独立 CLI 产品、MCP 服务或论文雷达，也没有假数据模式。事实核查和评测模块作为可选开发工具保留，尚未接入 Web 核查页面。普通使用者完成上面的步骤即可使用，无需额外配置这些工具。

需要检查代码时，在已安装依赖的 macOS / Linux 项目根目录执行：

```bash
bash verify.sh
```

它运行 Python 自动检查、前端代码检查和构建，失败时停止。检查使用测试专用模拟对象或真实 SDK 配合模拟 HTTP，不会调用真实模型或搜索；依赖安装仍可能联网。这是开发检查，不必每次写作前执行，也不能代替真实模型和页面验收。

需要修改页面并实时预览时，在项目根目录开启后台：

```bash
uv run --locked uvicorn server:app --reload --host 127.0.0.1 --port 8000
```

另开终端进入 `web/`，运行 `npm run dev -- --host 127.0.0.1`，Windows 使用 `npm.cmd run dev -- --host 127.0.0.1`。访问 Vite 输出的本机地址；结束时分别停止两个终端的进程。

**截至 2026-09-10 的验证记录：**GitHub Linux 环境的 Python 检查和前端 lint/build 已通过；Apple Silicon Mac 已完成新目录安装、自动检查、无配置首页展示，并在设置建连等待 20 秒后反馈 DeepSeek V4 Pro 生成大纲成功。完整教学、编辑、终审、下载及档案的真实模型验收仍待完成。Windows 和 Linux 完整使用流程尚未验收。

进一步阅读：[Mac 验收清单](docs/mac-validation.md)、[DeepSeek 配置与诊断](docs/deepseek.md)、[目录说明与迁移记录](docs/migration.md)、[评测工具说明](docs/evaluation.md)。

项目许可尚待发布前确认，当前未添加许可证。仓库公开与正式发布将在迁移验收后处理。

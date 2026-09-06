# 写作助手 · writing-tutor

先学懂，再用自己的话写作。输入选题后，研究员检索素材，策划安排提纲，导师逐节先讲再问，编辑把作者的回答整理成初稿，终审提出修改建议。终审不会替作者改写正文。

**当前是迁移后的开发起点，尚未完成新环境运行验收，暂不作为可直接发布的成品。** 无密钥演示启动、首次配置引导和跨平台启动方式将在下一阶段完善。

## 功能范围

- Web 页面：选择选题、确认或修改提纲、逐节问答、生成和下载初稿、查看作品档案。
- 学习模式：研究员通过 ddgs 搜索，导师依照素材和作者回答进行教学。
- 项目复盘模式：读取用户自己提供的本地素材，通过访谈整理实际经历。
- 可选的事实核查和评测开发工具；尚未增加事实核查的 Web 界面。

这是本地单用户程序。程序和作品文件保存在自己的电脑上；真实写作仍需访问配置的模型服务和搜索服务，相关素材及回答会发送给模型服务，可能产生费用。无需安装额外的个人网关项目。

## 当前安装与启动方式

以下是待运行环境验证的现有路径。需要 Python 3.12、uv、满足前端锁文件要求的 Node.js 和 npm；建议使用 Node.js 22.12 或以上的兼容版本。Python 依赖由 `uv.lock` 锁定，前端依赖由 `web/package-lock.json` 锁定。

在项目根目录打开终端：

```bash
uv sync --locked
cp .env.example .env
```

编辑 `.env`，填写自己的 `LLM_API_KEY`、`LLM_MODEL` 和服务要求的 `LLM_BASE_URL`。地址为空时，客户端使用 OpenAI 默认地址；密钥也可通过 `OPENAI_API_KEY` 提供。研究员所用模型需要支持工具调用。

**当前即使选择页面的假数据模式，后台导入时仍会读取模型配置。空白样例暂时不能直接启动服务。** 不要用演示截图来判断无密钥启动已经完成。

构建前端，然后用一个后台进程托管页面：

```bash
cd web
npm ci
npm run build
cd ..
uv run uvicorn server:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000`。修改配置后重启后台。Windows PowerShell 复制样例使用 `Copy-Item .env.example .env`；启动脚本的跨平台整理尚待下一阶段。

开发页面时可在依赖安装后运行 `bash run.sh`，由 Vite 提供页面并代理后台请求；这是已有的 Bash 开发入口，后续会整理进程退出与 Windows 启动方式。

## 配置与个人文件

| 项目 | 作用 |
|---|---|
| `.env` | 个人模型服务、密钥和可选作者背景，不提交 Git |
| `AUTHOR_PROFILE` | 可选的背景说明；留空时不预设作者的语言、职业或经验 |
| `local_artifacts/blogs/` | 默认存放素材、回答记录、提纲与草稿，不提交 Git |
| `MAKINGOF_MATERIAL` | 指向自己的素材文件；设置后整个服务使用项目复盘模式 |
| `MAKINGOF_OUT` | 复盘稿路径，默认 `local_artifacts/blogs/draft-makingof.md` |
| `GW_LEDGER` | 可选兼容网关的账本路径；不设置时不自动查找其他项目 |

私人素材请放在 `local_artifacts/`。如果把输出目录改到别处，需要自行确保它不会被提交。现有续写主要依靠文件识别已完成节，尚不保证刷新页面或后台重启后恢复所有对话状态。账单缺失时的界面表达也待完善。

## 文件说明与检查

[目录及迁移记录](docs/migration.md) 用简单语言说明各文件的作用和本次整理范围。[评测说明](docs/evaluation.md) 解释公开样例和检查方法。

完成依赖安装后，运行环境中的离线回归检查为：

```bash
uv run python _test_hardening.py
FORCE_STUB_FASTAPI=1 uv run python _test_hardening.py
uv run python _test_hardening_fc.py
uv run python _test_eval.py
cd web
npm run lint
npm run build
```

Python 检查使用模型与搜索替身，不需真实密钥；依赖安装本身需要联网。本次迁移仅完成源代码静态检查，以上运行检查尚待执行。

## 演示图片

以下为保留的假数据界面截图，用于展示交互，不是本次迁移后的运行测试记录。

![假数据模式首页](docs/shots/landing.png)

[逐节问答示例](docs/shots/tutoring.png) · [假初稿与终审示例](docs/shots/done.png)

项目许可尚待发布前确认，本迁移阶段未添加许可证。

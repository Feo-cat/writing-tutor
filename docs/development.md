# 开发说明

写作流程由 Python 后台驱动：研究员检索 → 策划提纲 → 导师逐节讲解和提问 → 编辑整理回答 → 终审给出建议。浏览器通过 HTTP 发起操作，通过 SSE 接收进度和生成结果。

## 项目结构

| 位置 | 职责 |
|---|---|
| `web/src/App.tsx`、`api.ts` | 界面、写作交互与后台通信 |
| `web/src/index.css`、`web/public/` | 页面样式与静态资源 |
| `server.py` | HTTP 接口、SSE、会话推进和作品档案 |
| `writing_assistant.py` | 研究、策划、导师、编辑与终审，以及统一的模型调用出口 |
| `makingof.py` | 基于用户提供的项目素材进行复盘写作 |
| `fact_checker.py`、`run_eval.py`、`eval_schema.py` | 可选事实核查、评测与用例格式校验，详见 [评测说明](evaluation.md) |
| `_test_*.py`、`_stubs/`、`_stubs_fallback/` | 回归检查与模型、搜索等外部依赖的测试替身 |
| `verify.sh`、`.github/workflows/tests.yml` | 本地检查入口与 GitHub 自动检查 |
| `.env.example`、`run.sh`、`run.ps1` | 配置样板与启动脚本 |

## 自动检查

按 [README](../README.md) 安装依赖后，在 macOS / Linux 或支持 Bash 的开发环境中，从项目根目录执行：

```bash
bash verify.sh
```

脚本运行 Python 回归检查及前端 lint/build，成功时输出 `Regression checks and frontend build completed.`。测试通过替身模拟模型和搜索请求，不访问真实服务。GitHub Actions 执行对应检查；纯 Markdown 或 `docs/` 的 push 按工作流配置跳过。

## 手动检查

涉及写作流程的修改，可按 README 启动服务，用一篇短文章检查以下行为。真实写作会请求模型和搜索服务，可能产生费用。

| 环节 | 检查内容 |
|---|---|
| 配置 | 首次未配置时显示缺项并禁止开始；填写配置、重启后可开始写作 |
| 研究与提纲 | 素材、提纲可读，来源可访问；重排意见生效，确认后进入问答 |
| 导师与编辑 | 先讲再问，作者回答后继续；初稿以作者回答为依据，保留个人表达 |
| 终审与下载 | 终审单独给出建议，下载的 Markdown 与页面初稿一致 |
| 保存与档案 | 文章完成后重启后台，可以从档案查看已保存内容 |

记录测试前的 `git rev-parse HEAD`、操作系统、模型、测试步骤和结果。遇到问题提供复现步骤、页面提示和相关错误行；密钥、私人素材及全文留在本机。未完成对话的恢复能力见 [README](../README.md#使用流程)。

## 开发约束

- 后台按本地单用户、单进程串行使用设计；模型配置、提示词及警告收集包含进程级状态。
- `makingof.py` 会切换引擎提示词，仅在启用项目复盘模式时导入。
- 编辑依据作者回答组织内容；终审给出建议，保留作者修改正文的决定权。
- `.env` 和作品保存在 Git 忽略的本地目录。依赖变更同步锁文件，样例变更同步版本与校验值。

具体代码约定见 [AGENTS.md](../AGENTS.md)。

#!/usr/bin/env bash
# 一条命令起开发环境：后端 uvicorn(:8000) + 前端 vite(:5173)。
# 跑起来后浏览器开 http://localhost:5173 （vite 会把 /api 代理到后端）。Ctrl+C 一起停。
#
# 想要「单进程」部署：先 cd web && npm run build，再单独 `uv run uvicorn server:app --port 8000`，
# 这时 8000 端口同时托管前端静态站，不必再开 vite。
set -e
cd "$(dirname "$0")"
trap 'kill 0' EXIT   # Ctrl+C 时把后端 / 前端一起带走

echo "▶ 后端 uvicorn → http://localhost:8000"
uv run uvicorn server:app --reload --port 8000 &

echo "▶ 前端 vite   → http://localhost:5173  （在这个地址用界面）"
cd web && npm run dev

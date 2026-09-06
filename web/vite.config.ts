import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// dev 时前端跑在 5173，后端 uvicorn 跑在 8000；
// 把 /api 代理到后端 → 浏览器看来是同源，免去 CORS。
// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8000' },
  },
})

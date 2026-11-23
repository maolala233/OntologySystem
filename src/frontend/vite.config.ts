import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
    plugins: [react()],
    server: {
        port: 5173,
        host: '0.0.0.0', 
        proxy: {
            '/api': {
                // 源码直起后端（uvicorn 3001，见 main.py）——默认即本地后端，
                // 需要指向别的地址（如容器化后端）时用 VITE_API_TARGET 覆盖
                target: process.env.VITE_API_TARGET || 'http://127.0.0.1:3001',
                changeOrigin: true,
            },
            // MCP 网关（HTTP Streamable）同域转发：client.ts 端点推导在非 3001 端口下
            // 返回相对路径 /mcp，缺这条代理会打到 vite 自身 404（空响应体→前端报 -32700）
            '/mcp': {
                target: process.env.VITE_API_TARGET || 'http://127.0.0.1:3001',
                changeOrigin: true,
            },
            // MinIO 下载（报告/PPT 模板等）：后端把预签名 URL 改写成同域 /minio/...，
            // 生产由 nginx 的 location /minio/ 转发并剥掉前缀；本地 vite 必须同样处理，
            // 否则 /minio/... 会落到 SPA 回退，点下载直接打开工作台（2026-11-23 实测）
            '/minio': {
                target: process.env.VITE_MINIO_TARGET || 'http://127.0.0.1:9010',
                changeOrigin: true,
                rewrite: (path) => path.replace(/^\/minio/, ''),
            }
        }
    },
    build: {
        // 生产构建配置
        rollupOptions: {
            output: {
                // 添加哈希值到文件名，确保缓存更新
                entryFileNames: 'assets/[name].[hash].js',
                chunkFileNames: 'assets/[name].[hash].js',
                assetFileNames: 'assets/[name].[hash].[ext]'
            }
        }
    }
})

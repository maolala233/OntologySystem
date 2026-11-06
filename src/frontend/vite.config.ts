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

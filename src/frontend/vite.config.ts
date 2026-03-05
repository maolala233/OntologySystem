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
                target: 'http://backend:3001',
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

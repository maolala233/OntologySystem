import axios from 'axios';

// 运行时动态获取API基础URL - 不依赖构建时的环境变量
const getApiBaseUrl = () => {
    // 方法1: 从URL参数获取（用于测试）
    const urlParams = new URLSearchParams(window.location.search);
    const apiHost = urlParams.get('api_host');
    if (apiHost) {
        return `http://${apiHost}:3001`;
    }
    
    // 方法2: 使用当前页面的host（推荐）
    const currentHost = window.location.host;
    
    // 如果是localhost或127.0.0.1，使用localhost:3001
    if (currentHost === 'localhost' || currentHost === '127.0.0.1') {
        return 'http://localhost:3001';
    }
    
    // 对于远程访问，移除端口号后加上:3001
    const hostWithoutPort = currentHost.replace(/:\d+$/, '');
    return `http://${hostWithoutPort}:3001`;
};

const API_BASE_URL = getApiBaseUrl();

console.log('API Base URL:', API_BASE_URL); // 调试用

const apiClient = axios.create({
    baseURL: API_BASE_URL,
    timeout: 600000,
});

// 请求拦截器 - 添加认证token
apiClient.interceptors.request.use(
    (config) => {
        const token = localStorage.getItem('access_token');
        if (token) {
            config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
    },
    (error) => {
        return Promise.reject(error);
    }
);

// 响应拦截器 - 处理认证错误
apiClient.interceptors.response.use(
    (response) => response,
    (error) => {
        if (error.response?.status === 401) {
            // Token过期或无效，清除本地存储并跳转到登录页
            localStorage.removeItem('access_token');
            localStorage.removeItem('user');
            window.location.href = '/login';
        }
        return Promise.reject(error);
    }
);

export default apiClient;
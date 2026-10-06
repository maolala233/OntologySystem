/**
 * M3-1/M3-2 文档面 API（03 §7）——上传/秒传/解析/分块/下载（Documents Tab 数据源）。
 */
import apiClient from './client';

export interface DocumentRow {
    id: number;
    filename: string;
    file_size: number | null;
    file_type: string | null;
    sha256: string | null;
    storage_key: string | null;
    text_key: string | null;
    parse_status: 'uploaded' | 'parsing' | 'parsed' | 'failed';
    parse_error: string | null;
    page_count: number | null;
    language: string | null;
    parse_backend: string | null;
    created_at: string | null;
    chunk_count?: number;
}

export interface DocumentChunkRow {
    chunk_index: number;
    text: string;
    char_start: number | null;
    char_end: number | null;
    token_count: number | null;
    meta: Record<string, any> | null;
}

export const documentsApi = {
    /** 多文件上传（秒传命中 → 409 DUPLICATE_UPLOAD） */
    upload: async (projectId: number, files: File[], autoParse = true): Promise<{
        saved: DocumentRow[];
        auto_parse: boolean;
        message?: string;
        duplicates?: any[];
    }> => {
        const formData = new FormData();
        files.forEach((f) => formData.append('files', f));
        const response = await apiClient.post(
            `/api/projects/${projectId}/documents/upload?auto_parse=${autoParse}`,
            formData,
            { headers: { 'Content-Type': 'multipart/form-data' } },
        );
        return response.data;
    },

    list: async (projectId: number, parseStatus?: string): Promise<{ total: number; documents: DocumentRow[] }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/documents`, {
            params: parseStatus ? { parse_status: parseStatus } : {},
        });
        return response.data;
    },

    /** 手动（重）解析：backend auto/docling_ocr/docling/vl_model/native + chunk 参数 */
    parse: async (projectId: number, docId: number, options?: {
        backend?: string;
        chunk_size?: number;
        overlap_ratio?: number;
    }): Promise<{ task_id: string }> => {
        const response = await apiClient.post(`/api/projects/${projectId}/documents/${docId}/parse`, options || {});
        return response.data;
    },

    /** 分块列表（游标分页，next_cursor=null 表示末页） */
    chunks: async (projectId: number, docId: number, cursor?: number, limit = 50): Promise<{
        chunks: DocumentChunkRow[];
        next_cursor: number | null;
    }> => {
        const response = await apiClient.get(`/api/projects/${projectId}/documents/${docId}/chunks`, {
            params: { cursor, limit },
        });
        return response.data;
    },

    /** 原件下载（302 预签名直跳）——新窗口打开 */
    downloadUrl: (projectId: number, docId: number): string =>
        `${apiClient.defaults.baseURL || ''}/api/projects/${projectId}/documents/${docId}/download`,

    /**
     * 原件下载（带鉴权）：新标签页直链无法携带 Authorization 头，会 401；
     * 改为 axios blob 拉流（302 预签名由浏览器自动跟随）后触发浏览器下载。
     */
    download: async (projectId: number, docId: number, filename?: string): Promise<void> => {
        const response = await apiClient.get(
            `/api/projects/${projectId}/documents/${docId}/download`,
            { responseType: 'blob' },
        );
        const name = filename || `document_${docId}`;
        const url = URL.createObjectURL(response.data as Blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    },

    /** 单个切片原文上下文（溯源"查看原文"高亮用） */
    chunkContext: async (projectId: number, docId: number, chunkIndex: number): Promise<{
        chunk: {
            document_id: number;
            document_name: string;
            chunk_index: number;
            text: string;
            char_start: number | null;
            char_end: number | null;
        };
    }> => {
        const response = await apiClient.get(
            `/api/projects/${projectId}/documents/${docId}/chunks/${chunkIndex}`,
        );
        return response.data;
    },

    remove: async (projectId: number, docId: number): Promise<void> => {
        await apiClient.delete(`/api/projects/${projectId}/documents/${docId}`);
    },

    clearAll: async (projectId: number): Promise<{ deleted_count: number }> => {
        const response = await apiClient.post(`/api/projects/${projectId}/documents/clear-all`);
        return response.data;
    },
};

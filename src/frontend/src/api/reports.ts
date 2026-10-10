// src/api/reports.ts - 本体报告/PPT 生成（R8 工具层）
import apiClient from './client';

export interface ReportOutline {
    title: string;
    subtitle?: string;
    slides: { title: string; bullets: (string | Record<string, unknown>)[] }[];
}

export interface ReportResult {
    kind: 'report' | 'ppt';
    title: string;
    content: string | null;
    outline: ReportOutline | null;
    downloads: Record<string, string>;
    latency_ms: number;
}

export async function generateReport(
    projectId: number,
    body: { kind: 'report' | 'ppt'; topic: string; max_slides?: number; use_template?: boolean; include_inferred?: boolean },
): Promise<ReportResult> {
    const resp = await apiClient.post(`/api/projects/${projectId}/reports/generate`, body, {
        timeout: 360_000,  // LLM 生成可能超过 90s
    });
    return resp.data;
}

/** 主题建议（LLM 基于图谱材料生成，后端缓存 10 分钟；失败回退知识锚点） */
export async function suggestTopics(projectId: number): Promise<string[]> {
    const resp = await apiClient.get(`/api/projects/${projectId}/reports/suggest`);
    return resp.data.chips ?? [];
}

// ── PPT 自定义模板（占位符协议：封面 {{title}}/{{subtitle}}/{{date}}/{{project}}，
//    内容页 {{slide_title}}+{{bullets}}，结尾页 {{closing}}）

export async function uploadPptTemplate(projectId: number, file: File): Promise<{ ok: boolean; filename: string }> {
    const form = new FormData();
    form.append('file', file);
    const resp = await apiClient.post(`/api/projects/${projectId}/reports/ppt-template`, form, {
        timeout: 120_000,
    });
    return resp.data;
}

export async function getPptTemplate(projectId: number): Promise<{ exists: boolean; filename?: string; size?: number }> {
    const resp = await apiClient.get(`/api/projects/${projectId}/reports/ppt-template`);
    return resp.data;
}

export async function deletePptTemplate(projectId: number): Promise<void> {
    await apiClient.delete(`/api/projects/${projectId}/reports/ppt-template`);
}

/** 下载当前已上传模板（返回预签名 URL） */
export async function downloadPptTemplate(projectId: number): Promise<string> {
    const resp = await apiClient.get(`/api/projects/${projectId}/reports/ppt-template/download`);
    return resp.data.url;
}

/** 下载内置示例模板（内含占位符协议说明页） */
export async function downloadSampleTemplate(projectId: number): Promise<string> {
    const resp = await apiClient.get(`/api/projects/${projectId}/reports/ppt-template/sample`);
    return resp.data.url;
}

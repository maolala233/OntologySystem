/**
 * 预签名直链下载：当前页拉流后走 <a download> 保存。
 *
 * 以前模板/报告下载直接 window.open(url, '_blank')：新标签页先白屏等响应，
 * 响应是附件时那个空白页还留在浏览器里（2026-11-24 实测「点下载先白屏」）。
 * 这里改为当前页 fetch（同域 /minio 预签名直链，不额外带 Authorization），
 * 拿到 Blob 后触发保存：不新开标签页、不闪白，HTTP 失败还能弹提示。
 */
import { triggerBlobDownload } from './blobDownload';

export async function downloadFromUrl(url: string, filename: string): Promise<void> {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`下载失败（HTTP ${resp.status}）`);
    triggerBlobDownload(await resp.blob(), filename);
}

/** 从预签名直链取文件名（路径末段，去掉导出对象名里的 uuid8- 前缀） */
export function filenameFromUrl(url: string, fallback: string): string {
    try {
        const last = new URL(url, window.location.origin).pathname.split('/').pop() || '';
        return decodeURIComponent(last).replace(/^[0-9a-f]{8}-/i, '') || fallback;
    } catch {
        return fallback;
    }
}

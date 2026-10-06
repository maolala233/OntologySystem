/**
 * Blob 下载辅助：带 Authorization 的 axios blob 响应统一触发浏览器保存。
 */

/** 从 Content-Disposition 解析文件名（filename* 优先，支持中文） */
export function resolveFilenameFromDisposition(
    contentDisposition: string | undefined,
    fallback: string,
): string {
    if (!contentDisposition) return fallback;
    const filenameStarMatch = contentDisposition.match(/filename\*\s*=\s*UTF-8''([^;\s]+)/i);
    if (filenameStarMatch && filenameStarMatch[1]) {
        try {
            return decodeURIComponent(filenameStarMatch[1].trim());
        } catch {
            return filenameStarMatch[1].trim();
        }
    }
    const filenameMatch = contentDisposition.match(/filename="([^"]+)"/i);
    if (filenameMatch && filenameMatch[1]) return filenameMatch[1];
    return fallback;
}

/** 触发浏览器下载一个 Blob */
export function triggerBlobDownload(blob: Blob, filename: string): void {
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', filename);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
}

/**
 * shared/format - 跨页面共用的展示格式化（O1-F4）
 */

/** 秒 → 人话时长（约 N 秒/分钟/小时）；无效值返回 null（不显示） */
export function humanizeEta(seconds?: number | null): string | null {
    if (seconds == null || !isFinite(seconds) || seconds <= 0) return null;
    if (seconds < 60) return `约 ${Math.max(1, Math.round(seconds))} 秒`;
    if (seconds < 3600) return `约 ${Math.ceil(seconds / 60)} 分钟`;
    return `约 ${(seconds / 3600).toFixed(1)} 小时`;
}

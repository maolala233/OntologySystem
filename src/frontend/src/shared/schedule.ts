/**
 * 定时构建时间的统一格式化（2026-11-16）。
 *
 * 后端 `/api/projects/*` 与 `/api/pipeline/status` 下发的 `scheduled_at` 都是带
 * `+00:00` 的 UTC ISO，`new Date()` 会自动转成本地时间。展示规则：今天/明天用
 * 相对词（用户最关心"还有多久"），更远的直接 `MM-DD HH:mm`。
 *
 * 「我的项目」表格与项目详情页的构建卡片共用这一份，避免两处各写一遍导致格式漂移。
 */

const pad2 = (n: number) => String(n).padStart(2, '0');

const dayStart = (d: Date) => {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    return x.getTime();
};

/** 短格式：今天 14:00 / 明天 09:30 / 11-18 09:30；无法解析返回空串 */
export const fmtSchedule = (iso?: string | null): string => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const hm = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    const diffDays = Math.round((dayStart(d) - dayStart(new Date())) / 86400000);
    if (diffDays === 0) return `今天 ${hm}`;
    if (diffDays === 1) return `明天 ${hm}`;
    return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${hm}`;
};

/** 完整格式：2026/11/16 14:00:00（悬停提示用） */
export const fmtScheduleFull = (iso?: string | null): string => {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('zh-CN');
};

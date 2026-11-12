/**
 * shared/projectStatus - 项目生命周期状态的唯一权威映射（O1-F1）
 * 收编原 BuilderPage STATUS_TAG / UserAdminPage STATUS_LABEL 及各页散落的
 * is_published 标签（此前 ready 一处 'green' 一处 'success'、published 一处
 * 'purple' 一处 'green'）。颜色同时是品牌色体系的一部分（见 ui-design 规范）。
 *
 * 状态机（2026-11 列表视图改版，后端 /api/projects/my 直接下发 status）：
 *   draft     草稿     未构建
 *   scheduled 待构建   定时构建已登记
 *   building  构建中   大模型后台构建中
 *   ready     已构建   本体建模已完成构建
 *   published 已发布   构建完成并发布到资产中心
 */

/** 平台品牌色（antd token colorPrimary 同源，App.tsx） */
export const BRAND_COLOR = '#5B8DEF';

/** 项目状态 → antd Tag 颜色/文案 */
export const PROJECT_STATUS: Record<string, { color: string; text: string; desc: string }> = {
    draft: { color: 'default', text: '草稿', desc: '已创建但还没构建过本体' },
    scheduled: { color: 'gold', text: '待构建', desc: '定时构建已登记' },
    building: { color: 'processing', text: '构建中', desc: '大模型正在后台构建' },
    ready: { color: 'success', text: '已构建', desc: '本体构建完成，还未发布' },
    published: { color: 'purple', text: '已发布', desc: '构建完成并已发布到资产中心' },
};

/**
 * 项目状态归一：后端 /api/projects/my 直接下发 status（draft/scheduled/building/ready/published）；
 * 旧数据没有 status 时按 is_published 回退。所有展示状态的地方都走这里，避免「已构建」被当成「草稿」。
 */
export const projectStatusOf = (p: { status?: string; is_published: boolean }): string =>
    p.status || (p.is_published ? 'published' : 'draft');

/** 状态归一 + 文案/颜色映射，一步取到 Tag 需要的 meta */
export const projectStatusMeta = (p: { status?: string; is_published: boolean }) =>
    PROJECT_STATUS[projectStatusOf(p)] ?? PROJECT_STATUS.draft;

/** 二值发布标签（仅有 is_published 布尔上下文时使用） */
export const PUBLISHED_TAG = PROJECT_STATUS.published;

/** 公理来源 → 标签（2026-11 结果总览：从哪来 + 依据什么） */
export const AXIOM_ORIGIN: Record<string, { color: string; text: string }> = {
    document: { color: 'geekblue', text: 'AI 文档抽取' },
    statistical: { color: 'cyan', text: '数据统计推断' },
    derived: { color: 'purple', text: '公理推导' },
    schema: { color: 'default', text: '抽取声明' },
    manual: { color: 'default', text: '人工声明' },
};

/** 「有新版本」叠加标记（已发布后重新构建过：资产中心还是旧版，可重新发布） */
export const STALE_PUBLISHED_TAG = { color: 'orange', text: '有新版本' };

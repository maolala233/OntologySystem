/**
 * shared/projectStatus - 项目生命周期状态的唯一权威映射（O1-F1）
 * 收编原 BuilderPage STATUS_TAG / UserAdminPage STATUS_LABEL 及各页散落的
 * is_published 标签（此前 ready 一处 'green' 一处 'success'、published 一处
 * 'purple' 一处 'green'）。颜色同时是品牌色体系的一部分（见 ui-design 规范）。
 *
 * 状态机（2026-11 列表视图改版，后端 /api/projects/my 直接下发 status）：
 *   draft     草稿     未构建
 *   building  构建中   大模型后台构建中（含排队/定时未开跑）
 *   ready     已构建   本体建模已完成构建
 *   published 已发布   构建完成并发布到资产中心
 */

/** 平台品牌色（antd token colorPrimary 同源，App.tsx） */
export const BRAND_COLOR = '#5B8DEF';

/** 项目状态 → antd Tag 颜色/文案 */
export const PROJECT_STATUS: Record<string, { color: string; text: string }> = {
    draft: { color: 'default', text: '草稿' },
    building: { color: 'processing', text: '构建中' },
    ready: { color: 'success', text: '已构建' },
    published: { color: 'purple', text: '已发布' },
};

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

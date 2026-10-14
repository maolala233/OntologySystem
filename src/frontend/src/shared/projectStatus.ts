/**
 * shared/projectStatus - 项目生命周期状态的唯一权威映射（O1-F1）
 * 收编原 BuilderPage STATUS_TAG / UserAdminPage STATUS_LABEL 及各页散落的
 * is_published 标签（此前 ready 一处 'green' 一处 'success'、published 一处
 * 'purple' 一处 'green'）。颜色同时是品牌色体系的一部分（见 ui-design 规范）。
 */

/** 平台品牌色（antd token colorPrimary 同源，App.tsx） */
export const BRAND_COLOR = '#5B8DEF';

/** 项目状态 → antd Tag 颜色/文案 */
export const PROJECT_STATUS: Record<string, { color: string; text: string }> = {
    draft: { color: 'default', text: '草稿' },
    building: { color: 'processing', text: '构建中' },
    ready: { color: 'success', text: '就绪' },
    published: { color: 'purple', text: '已发布' },
};

/** 二值发布标签（仅有 is_published 布尔上下文时使用） */
export const PUBLISHED_TAG = PROJECT_STATUS.published;

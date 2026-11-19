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
 * 冷调状态徽标配色（2026-11-17）：项目列表和详情页的状态胶囊共用这一份。
 * 原来直接用 antd 预设色 Tag（success 淡绿 / purple 淡紫 / gold 琥珀），
 * 和全站自定义的冷调标签（文档解析状态、构建阶段、知识域）放在一起像两套语言，
 * 尤其详情页里状态和蓝色的「知识域」标签紧挨着时最明显。
 * 这里按「底色 / 文字 / 描边」三件套统一，饱和度比预设色低一档，文字取深一档。
 * failed / cancelled 不是后端生命周期状态，但界面按最近一次管线终态覆盖显示，一并给出。
 */
export const STATUS_CHIP: Record<string, { background: string; color: string; borderColor: string }> = {
    draft: { background: 'rgba(15,23,42,0.05)', color: '#8A94AB', borderColor: 'rgba(15,23,42,0.08)' },
    scheduled: { background: 'rgba(242,185,80,0.18)', color: '#B8790F', borderColor: 'rgba(242,185,80,0.45)' },
    building: { background: 'rgba(91,141,239,0.12)', color: '#2A57C6', borderColor: 'rgba(91,141,239,0.28)' },
    ready: { background: 'rgba(27,135,173,0.10)', color: '#1B87AD', borderColor: 'rgba(27,135,173,0.24)' },
    published: { background: 'rgba(139,92,246,0.14)', color: '#6D3FD8', borderColor: 'rgba(139,92,246,0.30)' },
    failed: { background: 'rgba(180,35,24,0.10)', color: '#B42318', borderColor: 'rgba(180,35,24,0.22)' },
    cancelled: { background: 'rgba(242,185,80,0.18)', color: '#B8790F', borderColor: 'rgba(242,185,80,0.45)' },
    // 停止中（后端 pipeline status=cancelling）：已发取消、等 worker 退出，仍持运行锁
    stopping: { background: 'rgba(242,185,80,0.20)', color: '#B8790F', borderColor: 'rgba(242,185,80,0.48)' },
};

/** 取状态徽标配色（未知状态回落到草稿） */
export const statusChip = (key?: string | null) => STATUS_CHIP[key || 'draft'] || STATUS_CHIP.draft;

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

/** 「有新版本」叠加标记（已发布后重新构建过：资产中心还是旧版，可重新发布）。
 *  角标色 2026-11-17 跟状态徽标一起收到冷调琥珀，不再用 antd 的 orange 关键字 */
export const STALE_PUBLISHED_TAG = { color: '#B8790F', text: '有新版本' };

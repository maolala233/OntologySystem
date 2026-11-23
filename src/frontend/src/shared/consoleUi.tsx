/**
 * 管理后台 / 工具页共用小件（2026-11-23）
 *
 * 报告生成、PPT 生成与「系统配置」五个菜单原先各自拼装标题、Tag、空态和提示条，
 * 字号、色温、圆角都有细微差异。这里把「面板装饰语言」收成一份可复用的小件：
 * 小标题彩条 + 发光圆点 + 彩色等宽数字 + 竖线分列（指标带）。
 *
 * 颜色只用站内已有色相：品牌蓝 / 紫 / 青 / 琥珀 / 冷调红 / 灰，
 * 与 shared/projectStatus 的 STATUS_CHIP 同一组值，不引入新色。
 */
import React, { useId } from 'react';
import { InfoCircleOutlined, WarningOutlined } from '@ant-design/icons';
import DecoConstellation from '../components/DecoConstellation';

export type ConsoleTone = 'brand' | 'violet' | 'cyan' | 'amber' | 'danger' | 'quiet';

/** 每种语气对应的文字主色（等宽数字、图标块文字） */
export const TONE_INK: Record<ConsoleTone, string> = {
    brand: '#2A57C6',
    violet: '#6D3FD8',
    cyan: '#1B87AD',
    amber: '#B8790F',
    danger: '#B42318',
    quiet: '#8A94AB',
};

/** 每种语气对应的淡底（图标块、计数底） */
const TONE_TINT: Record<ConsoleTone, string> = {
    brand: 'rgba(91, 141, 239, 0.12)',
    violet: 'rgba(139, 92, 246, 0.12)',
    cyan: 'rgba(27, 135, 173, 0.10)',
    amber: 'rgba(242, 185, 80, 0.18)',
    danger: 'rgba(180, 35, 24, 0.10)',
    quiet: 'rgba(15, 23, 42, 0.05)',
};

/** 发光圆点：指标带标签、状态行、脏数据提示共用 */
export const Dot: React.FC<{ tone?: ConsoleTone; className?: string }> = ({
    tone = 'brand', className = '',
}) => <span className={`tk-dot tk-dot--${tone} ${className}`} aria-hidden />;

/** 冷调标签胶囊：替代 antd 预设色 Tag；mono 让编号/倍率走等宽字形 */
export const TkTag: React.FC<{
    tone?: ConsoleTone;
    mono?: boolean;
    className?: string;
    children: React.ReactNode;
}> = ({ tone = 'brand', mono, className = '', children }) => (
    <span className={`tk-tag tk-tag--${tone} ${mono ? 'tk-mono' : ''} ${className}`}>
        {children}
    </span>
);

export interface StatItem {
    label: React.ReactNode;
    value: React.ReactNode;
    tone?: ConsoleTone;
    icon?: React.ReactNode;
    hint?: React.ReactNode;
}

/**
 * 指标带：一条冷蓝纸 + 柔光 + 节点线稿，竖线分列。
 * cols 控制宽屏列数（窄屏固定两列），值走等宽数字、颜色按语气取。
 * 与 BuildResultsPanel 的结构指标带是同一件东西，这里收成可复用组件。
 */
export const StatBand: React.FC<{ items: StatItem[]; cols?: number; className?: string }> = ({
    items, cols, className = '',
}) => {
    const uid = useId().replace(/:/g, '');
    return (
        <div className={`tk-stat-band ${className}`}>
            <DecoConstellation
                id={`console-stat-${uid}`}
                className="pointer-events-none absolute -bottom-6 -right-8 h-[130px] w-[320px] opacity-60"
                style={{ transform: 'scaleY(-1)' }}
            />
            <div
                className="tk-stat-grid relative"
                style={{ '--tk-stat-cols': cols ?? items.length } as React.CSSProperties}
            >
                {items.map((item, i) => {
                    const tone = item.tone ?? 'brand';
                    return (
                        <div key={i} className="tk-stat-cell">
                            <div className="min-w-0">
                                <div className="flex items-center gap-1.5 text-[11.5px] leading-4 text-[#8A94AB]">
                                    <Dot tone={tone} />
                                    <span className="truncate">{item.label}</span>
                                </div>
                                <div
                                    className="tk-mono mt-1.5 text-[22px] font-semibold leading-none"
                                    style={{ color: TONE_INK[tone] }}
                                >
                                    {item.value}
                                </div>
                                {item.hint && (
                                    <div className="mt-1 truncate text-[11px] text-[#A3ADC2]">{item.hint}</div>
                                )}
                            </div>
                            {item.icon && (
                                <span
                                    className="tk-stat-icon"
                                    style={{ background: TONE_TINT[tone], color: TONE_INK[tone] }}
                                >
                                    {item.icon}
                                </span>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

/** 面板小标题：3×13px 蓝→紫竖条 + 深墨标题 + 可选说明 / 右侧操作 */
export const SectionHead: React.FC<{
    title: React.ReactNode;
    hint?: React.ReactNode;
    extra?: React.ReactNode;
    className?: string;
}> = ({ title, hint, extra, className = '' }) => (
    <div className={`flex flex-wrap items-center gap-x-2 gap-y-1 ${className}`}>
        <span className="tk-mark" aria-hidden />
        <span className="text-[13px] font-semibold text-[#1F2A44]">{title}</span>
        {hint && <span className="text-[11.5px] text-[#8A94AB]">{hint}</span>}
        {extra && <span className="ml-auto flex flex-wrap items-center gap-2">{extra}</span>}
    </div>
);

/** 页面级说明条：冷调纸面 + 图标 + 正文，替代页面里裸的 antd Alert */
export const Note: React.FC<{
    tone?: ConsoleTone;
    icon?: React.ReactNode;
    className?: string;
    children: React.ReactNode;
}> = ({ tone = 'brand', icon, className = '', children }) => {
    const variant = tone === 'brand' || tone === 'quiet' ? '' : ` tk-note--${tone}`;
    const fallback = tone === 'amber' || tone === 'danger'
        ? <WarningOutlined />
        : <InfoCircleOutlined />;
    return (
        <div className={`tk-note${variant} ${className}`}>
            {icon ?? fallback}
            <div className="min-w-0">{children}</div>
        </div>
    );
};

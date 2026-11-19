/**
 * 深色图谱工作区的加载骨架（2026-11-19）
 *
 * 资产详情 / 构建结果共用。这两个页面加载完是「浅色外壳 + 内缩 16px 圆角的深色台面」，
 * 原来加载态是浅色整屏 + 居中转圈：数据一回来，一整块深色面积凭空浮上来，
 * 这是进入时最明显的一次跳变。
 *
 * 现在从第一帧就按最终构图铺出来——同样的内缩、圆角、投影，左栏信息面板用低对比骨架线，
 * 画布区放一枚冷色状态胶囊。数据回来是在原位填充，不再有「深色台面突然出现」的瞬间。
 *
 * 骨架线用静态 + 轻微脉冲，不做扫光：深底上一道亮光扫过去比静止更吵。
 */
import React from 'react';
import { LoadingOutlined } from '@ant-design/icons';
import Navbar from './Navbar';

interface WorkspaceSkeletonProps {
    breadcrumbs: { title: string; path?: string }[];
    /** 左栏信息面板宽度（资产详情 400；构建结果 360 / 2xl 400） */
    panelClass?: string;
}

export default function WorkspaceSkeleton({
    breadcrumbs,
    panelClass = 'w-[360px] 2xl:w-[400px]',
}: WorkspaceSkeletonProps) {
    return (
        <div className="h-full flex flex-col app-canvas">
            <Navbar breadcrumbs={breadcrumbs} />
            <div
                className="flex flex-1 min-h-0 overflow-hidden m-4 sm:m-5"
                role="status"
                aria-live="polite"
                aria-label="正在载入图谱"
                style={{
                    // 与 .asset-workspace 同一套材质，只不过这里内联：加载分支还没挂页面样式表
                    borderRadius: 16,
                    background: '#0F1420',
                    border: '1px solid rgba(15, 23, 42, 0.22)',
                    boxShadow:
                        '0 28px 60px -34px rgba(15, 23, 42, 0.62), '
                        + '0 8px 18px -12px rgba(15, 23, 42, 0.28), '
                        + 'inset 0 1px 0 rgba(255, 255, 255, 0.06)',
                }}
            >
                {/* 左栏：信息面板占位 */}
                <div className={`${panelClass} shrink-0 overflow-hidden p-4`} style={{ borderRight: '1px solid #1A2233' }}>
                    <div
                        className="h-full rounded-[16px] border p-4"
                        style={{
                            borderColor: 'rgba(255, 255, 255, 0.085)',
                            backgroundImage:
                                'radial-gradient(460px 280px at -10% -8%, rgba(91, 141, 239, 0.20), transparent 70%),'
                                + 'radial-gradient(460px 320px at 112% 116%, rgba(139, 92, 246, 0.14), transparent 72%),'
                                + 'linear-gradient(165deg, #1E2740 0%, #182035 55%, #151D2F 100%)',
                            boxShadow: 'inset 0 1px 0 rgba(255, 255, 255, 0.08)',
                        }}
                    >
                        <div className="animate-pulse">
                            {/* 身份段：彩条 + 标题 */}
                            <div className="flex items-center gap-2">
                                <span
                                    className="h-[13px] w-[3px] rounded-full"
                                    style={{ background: 'linear-gradient(180deg, #5B8DEF 0%, #8B5CF6 100%)' }}
                                />
                                <span className="h-3.5 w-1/2 rounded bg-white/[0.12]" />
                            </div>
                            {/* 状态 / 知识域 / 版本 一类的胶囊 */}
                            <div className="mt-3 flex gap-1.5">
                                <span className="h-[14px] w-12 rounded-full bg-white/[0.09]" />
                                <span className="h-[14px] w-16 rounded-full bg-white/[0.06]" />
                                <span className="h-[14px] w-10 rounded-full bg-white/[0.06]" />
                            </div>
                            <div className="my-3.5 h-px bg-white/[0.08]" />
                            {/* 指标行：左标签 + 右数值 */}
                            <div className="space-y-2.5">
                                {[72, 48, 88, 56].map((w, i) => (
                                    <div key={i} className="flex items-center justify-between gap-3">
                                        <span className="h-2.5 w-14 rounded bg-white/[0.06]" />
                                        <span className="h-2.5 rounded bg-white/[0.10]" style={{ width: w }} />
                                    </div>
                                ))}
                            </div>
                            <div className="my-3.5 h-px bg-white/[0.08]" />
                            {/* 段落：小标题 + 两行正文 */}
                            <div className="space-y-2">
                                <span className="block h-2.5 w-1/4 rounded bg-white/[0.10]" />
                                <span className="block h-2.5 w-full rounded bg-white/[0.06]" />
                                <span className="block h-2.5 w-4/5 rounded bg-white/[0.06]" />
                            </div>
                        </div>
                    </div>
                </div>
                {/* 右侧：画布占位 */}
                <div className="flex min-w-0 flex-1 items-center justify-center">
                    <div
                        className="flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[12px]"
                        style={{
                            color: '#8FA0C4',
                            background: 'rgba(255, 255, 255, 0.04)',
                            border: '1px solid rgba(255, 255, 255, 0.08)',
                        }}
                    >
                        <LoadingOutlined style={{ color: '#7FA8F2' }} />
                        正在载入图谱…
                    </div>
                </div>
            </div>
        </div>
    );
}

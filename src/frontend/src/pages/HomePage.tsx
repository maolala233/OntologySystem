import React from 'react';
import { Button } from 'antd';
import {
    RocketOutlined,
    ArrowRightOutlined,
    ApartmentOutlined,
    ShareAltOutlined,
    BookOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../shared/auth/authStore';
import Navbar from '../components/Layout/Navbar';
import TechHero from '../components/TechHero';

const C = {
    purple: '#B585F2',
    blue: '#5B8DEF',
    cyan: '#5AC8FA',
};

/** 面板内部的分隔线：横向分「段」，竖线分「列」，都通到面板边缘。
 *  横向比竖线略深一档——段落之间需要被看见，列之间只需要被暗示。
 *  板底改成冷蓝后，原来那组近白的灰线会「消失」，整体加深一档 */
const HAIRLINE_H = 'border-[#DCE5F2]';
const HAIRLINE_V = 'border-[#E2EAF6]';

/**
 * 工作台（首页）。
 *
 * 整页两个「面」：上面是独立浮起的 Hero 门面，下面是一张说明板。
 *
 * 版式演进：
 *   旧版 —— 四张统计卡 + 三张功能卡 + 一张指南卡，八块同权重的卡片各自带描边/投影/渐变底，
 *           眼睛拿不到分组，观感碎；
 *   2026-11-17 —— 统计收成一条数据条、功能与指南各自成块；
 *   2026-11-17 再收 —— 三者并进同一张面板，彼此只用细线分段。
 *
 * 板内顺序（2026-11-17 定稿）：
 *   核心功能 → 快速开始。
 *   把入口放在最上面是因为这是一张「工作台」：用户落下来第一件事是去干活。
 *
 * 2026-11-17 去掉「平台概览」：那四个数字（我的项目 / 已发布 / 公共资产 / 图谱节点）
 * 在「我的项目」页顶部的状态统计里本来就有一份，口径还更细、且可直接点着筛；
 * 公共资产在资产中心有完整列表。工作台的职责是「去哪儿」而不是「我有什么」，
 * 这块内容撑不起一条横贯整板的带子——前几轮反复调它的图标位置、居中、加到六个，
 * 本质原因是内容不该占这个版面。顺带省掉进入首页时的两次统计接口请求。
 */
const HomePage: React.FC = () => {
    const navigate = useNavigate();

    const { user, modules } = useAuthStore();
    // 模块可见性：admin 全量；普通用户按授权矩阵；矩阵未加载前放行（避免首屏闪烁）
    const canUse = (code: string) =>
        user?.role === 'admin' ? true : modules.length > 0 ? modules.includes(code) : true;

    // 三张卡各去不同页面：智能提取/可视化编辑本是同一项目内的前后两步，合并为「本体构建」，
    // 第三张补上平台已具备、首页原先缺入口的「本体问答」（2026-11-11）
    // 2026-11-11：按模块授权过滤——入口指向的页面在左侧菜单没有对应项时不再展示，
    // 避免点进去落到一个「没有归属菜单」的页面（路由侧另加了 RequireModule 兜底）
    // 2026-11-17：ink / deep 是图标徽标的渐变两端（深色底 + 白图标才立得住），
    // color 只用来做徽标下面那层同色柔光；ink 同时是「进入」的文字色。
    // 三者同色相、深浅分工，避免三个入口各刷一套不相干的颜色
    const features = [
        {
            module: 'projects',
            icon: <ApartmentOutlined />,
            color: C.blue,
            ink: '#2F62D8',
            deep: '#244FB8',
            title: '本体构建',
            description: '上传文档 AI 提取结构 · 骨架画布精修 · 实例图谱探索',
            action: () => navigate('/my-projects'),
        },
        {
            module: 'asset_center',
            icon: <ShareAltOutlined />,
            color: C.cyan,
            ink: '#1B87AD',
            deep: '#146E8E',
            title: '协作共享',
            description: '版本快照、审核门禁，发布到公共资产中心',
            action: () => navigate('/asset-center'),
        },
        {
            module: 'qa',
            icon: <BookOutlined />,
            color: C.purple,
            ink: '#6D3FD8',
            deep: '#5A2FBA',
            title: '本体问答',
            description: '基于已建本体的自然语言问答，答案可溯源到图谱',
            action: () => navigate('/qa'),
        },
    ].filter((f) => canUse(f.module));

    // 步骤不再各配一套色相：一条轨道 + 一个品牌色，读作「一条流程」而不是四段并排文字
    const steps = [
        { title: '创建项目', desc: '在「我的项目」中创建一个新的本体建模项目' },
        { title: '上传文档', desc: '上传相关文档，AI 将自动提取本体结构' },
        { title: '可视化调整', desc: '在画布上拖拽、编辑节点和关系，完善本体模型' },
        { title: '发布共享', desc: '发布到图数据库，并在资产中心公开展示' },
    ];

    const breadcrumbs = [{ title: '工作台' }];

    return (
        <div className="h-full flex flex-col app-canvas">
            <style>{`
                .hp-mono { font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace; }
                /* 段落标题前的短彩条（2026-11-17）：两段标题左侧各一枚，
                   给这块以深色文字为主的板面加一条竖向的彩色节奏，不占额外空间 */
                .hp-section-mark {
                    width: 3px;
                    height: 14px;
                    border-radius: 999px;
                    background: linear-gradient(180deg, #5B8DEF 0%, #8B5CF6 100%);
                    flex-shrink: 0;
                }
                /* 功能列：同一张面板内三列（2026-11-17）。颜色只留在图标和箭头，
                   hover 只给该列一层极淡品牌蓝漫染——面板上不再有卡片抬升 */
                .hp-feature-col {
                    --hp-ink: #2A57C6;
                    cursor: pointer;
                    transition: background-color .2s ease;
                }
                .hp-feature-col:hover { background-color: rgba(91, 141, 239, 0.07); }
                .hp-feature-col:focus-visible {
                    background-color: rgba(91, 141, 239, 0.10);
                    outline: none;
                }
                .hp-feature-col:active { background-color: rgba(91, 141, 239, 0.13); }
                /* 「进入」默认就带该入口的主题色（原来灰到 hover 才上色，等于把颜色藏起来了），
                   hover 时再加粗一级并把箭头顶出去 */
                .hp-feature-cta { color: var(--hp-ink); opacity: .78; transition: opacity .2s ease; }
                .hp-feature-col:hover .hp-feature-cta,
                .hp-feature-col:focus-visible .hp-feature-cta { opacity: 1; }
                .hp-feature-cta-arrow { transition: transform .2s ease; }
                .hp-feature-col:hover .hp-feature-cta-arrow,
                .hp-feature-col:focus-visible .hp-feature-cta-arrow { transform: translateX(3px); }
            `}</style>
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                {/* ── 面一：门面。Hero 独立浮起，只放标题、副标题与两个主操作 ── */}
                <TechHero
                    tone="light"
                    className="!mb-6"
                    title="企业级语义知识及本体治理平台"
                    titleSuffix="WORKBENCH"
                    subtitle="基于 AI 的本体构建 · 知识图谱 · 溯源治理一站式流水线，让知识资产沉淀为可计算的服务"
                    actions={
                        (canUse('projects') || canUse('asset_center')) ? (
                            <>
                                {canUse('projects') && (
                                    <Button
                                        type="primary"
                                        icon={<RocketOutlined />}
                                        onClick={() => navigate('/my-projects')}
                                        className="h-9 sm:h-10 px-4 sm:px-6 border-0"
                                        style={{ background: `linear-gradient(95deg, ${C.blue}, ${C.purple})`, boxShadow: '0 8px 20px -10px rgba(91,141,239,0.55)' }}
                                    >
                                        开始使用
                                    </Button>
                                )}
                                {canUse('asset_center') && (
                                    <Button
                                        icon={<ArrowRightOutlined />}
                                        onClick={() => navigate('/asset-center')}
                                        className="tk-soft-btn h-9 sm:h-10 px-4 sm:px-6"
                                    >
                                        浏览资产中心
                                    </Button>
                                )}
                            </>
                        ) : undefined
                    }
                />

                {/* ── 面二：说明板。入口 / 上手 / 数据同属「关于这个平台」的内容，
                    收进同一张面板，彼此只用细线分段。
                    底子：均匀冷蓝渐变 + 两个大号柔光 + 20px 细点阵。
                    刻意不用「光带/色块」这类有明确边界的装饰——它们会在板面上切出一道明暗分界，
                    把注意力从内容上拽走；板面只留一层均匀的、退到背后的「纸」，
                    装饰交给上方 Hero 右侧那张图去承担 ── */}
                <section
                    className="surface-card !rounded-2xl"
                    aria-label="平台能力与上手路径"
                    style={{
                        backgroundImage:
                            // 1) 细点阵：20px 网格 —— 近看是纸，远看只是一层很淡的颗粒
                            'radial-gradient(circle at 1px 1px, rgba(47,98,216,0.11) 1px, transparent 0),' +
                            // 2) 左上柔光：放大到 760×460、衰减拉到 68%，边界化在整块板里而不是一个圆
                            'radial-gradient(760px 460px at -8% -12%, rgba(91,141,239,0.20), transparent 68%),' +
                            // 3) 右下柔紫光：和左上对角呼应，让板面右半边不至于是一片平色
                            'radial-gradient(680px 520px at 106% 118%, rgba(139,92,246,0.14), transparent 70%),' +
                            // 4) 板底：冷蓝，没有一处纯白
                            'linear-gradient(160deg, #F2F7FF 0%, #EAF2FE 52%, #F1F6FF 100%)',
                        backgroundSize: '20px 20px, auto, auto, auto',
                    }}
                >
                    {/* 第一段：核心功能（权限过滤后少于 2 张时整段隐藏：
                        只剩 1 张会空出半栏，0 张只剩一个空标题，都不好看，2026-11-12） */}
                    {features.length >= 2 && (
                        <section aria-labelledby="hp-features">
                            <h2
                                id="hp-features"
                                className="flex items-center gap-2 px-5 sm:px-6 lg:px-7 pt-5 text-[15px] font-semibold text-[#0F172A]"
                            >
                                <span className="hp-section-mark" aria-hidden />
                                核心功能
                            </h2>
                            <div className={`grid ${features.length === 3 ? 'md:grid-cols-3' : 'md:grid-cols-2'}`}>
                                {features.map((feature, i) => (
                                    // 整列可点（不是 <button>：标题要用 h3，button 只允许短语内容）。
                                    // 键盘可达：Enter / 空格与点击同效
                                    <div
                                        role="button"
                                        tabIndex={0}
                                        key={feature.title}
                                        onClick={feature.action}
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter' || e.key === ' ') {
                                                e.preventDefault();
                                                feature.action();
                                            }
                                        }}
                                        className={[
                                            'hp-feature-col flex h-full w-full flex-col px-5 sm:px-6 lg:px-7 py-5 text-left',
                                            i > 0 ? `md:border-l ${HAIRLINE_V}` : '',
                                        ].join(' ')}
                                        style={{ '--hp-ink': feature.ink } as React.CSSProperties}
                                    >
                                        <div className="flex items-start gap-3.5">
                                            <span
                                                className="mt-0.5 w-11 h-11 shrink-0 rounded-[12px] flex items-center justify-center text-[18px] text-white"
                                                style={{
                                                    // 文字色改用同色相的深一档（ink），图标反过来用白：
                                                    // 淡底上的淡图标永远「没精神」，实心 + 白字才立得住
                                                    background: `linear-gradient(140deg, ${feature.ink} 0%, ${feature.deep} 100%)`,
                                                    boxShadow: `0 10px 20px -12px ${feature.color}, inset 0 1px 0 rgba(255,255,255,0.28)`,
                                                }}
                                            >
                                                {feature.icon}
                                            </span>
                                            <div className="min-w-0">
                                                <h3 className="text-[15.5px] font-semibold leading-tight text-[#0F172A]">{feature.title}</h3>
                                                <p className="mt-1.5 text-[12.5px] leading-[1.6] text-[#66738F]">{feature.description}</p>
                                            </div>
                                        </div>
                                        {/* mt-auto：描述长短不一时，三列的「进入」仍压在同一条基线上 */}
                                        <div className="hp-feature-cta mt-auto pt-3.5 inline-flex items-center gap-1.5 text-[12.5px] font-medium">
                                            进入 <ArrowRightOutlined className="text-[10px] hp-feature-cta-arrow" />
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </section>
                    )}

                    {/* 第二段：快速开始。一条编号轨道，四步之间用渐隐细线连起来，读作一条流程 */}
                    <section
                        className={features.length >= 2 ? `border-t ${HAIRLINE_H}` : ''}
                        aria-labelledby="hp-steps"
                    >
                        <div className="px-5 sm:px-6 lg:px-7 pt-5 pb-6">
                            <h2 id="hp-steps" className="flex items-center gap-2 text-[15px] font-semibold text-[#0F172A]">
                                <span className="hp-section-mark" aria-hidden />
                                快速开始指南
                            </h2>
                            <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-x-6 gap-y-6">
                                {steps.map((step, i) => (
                                    <div key={step.title}>
                                        <div className="flex items-center gap-2.5">
                                            <span
                                                className="hp-mono w-7 h-7 shrink-0 rounded-full flex items-center justify-center text-[11px] font-bold text-white"
                                                style={{
                                                    // 编号用品牌蓝→紫的实心渐变（和 Hero 主按钮同一套），
                                                    // 四枚小色点在版面上撑起一处明确的彩色节奏
                                                    background: 'linear-gradient(135deg, #5B8DEF 0%, #8B5CF6 100%)',
                                                    boxShadow: '0 8px 16px -10px rgba(91, 141, 239, 0.85)',
                                                }}
                                            >
                                                {i + 1}
                                            </span>
                                            {i < steps.length - 1 && (
                                                // 四列（xl）时四步连成一条线；两列（sm~xl）时只连同一行内的
                                                // 1→2、3→4——跨行的 2→3 要画折线，反而更乱，留白更好读
                                                <span
                                                    aria-hidden
                                                    className={`${i === 1 ? 'hidden xl:block' : 'hidden sm:block'} flex-1 h-px`}
                                                    style={{ background: 'linear-gradient(90deg, rgba(91,141,239,.30), rgba(91,141,239,.04))' }}
                                                />
                                            )}
                                        </div>
                                        <h4 className="mt-3 text-[13.5px] font-semibold leading-tight text-[#0F172A]">{step.title}</h4>
                                        <p className="mt-1 text-[12px] leading-[1.65] text-[#66738F]">{step.desc}</p>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </section>
                </section>
            </div>
        </div>
    );
};

export default HomePage;

// components/Layout/DemoGuide.tsx - 演示模式脚本条（FE12，docs/16）
// Navbar 开关（localStorage 'demo_mode' + demo-mode-changed 事件）控制显隐；
// 六步演示脚本：每步一句"做什么+去哪里"，答辩/新用户照着走不迷路。
import React, { useEffect, useState } from 'react';
import { Button, Card, Tag } from 'antd';
import { PlayCircleOutlined, CloseOutlined } from '@ant-design/icons';

const STEPS: { title: string; hint: string; path?: string }[] = [
    { title: '① 上传并解析文档', hint: '项目 1807 已就绪；新项目则在「文档」页签上传 PPT/PDF/DOCX，解析自动写向量库', path: '/projects' },
    { title: '② 审核抽取结果', hint: '「审核」工作台逐条/批量认证：AI 徽标 → 人工认证后重跑不覆盖', path: '/reviews' },
    { title: '③ 发布门禁', hint: '「时间轴 → 发布」预检：机检链四项逐行结论 + 证据闭合 + 配对评估，通过才可发布' },
    { title: '④ 智能体查语义', hint: 'MCP 工具页发令牌；Agent 调 resolve_semantics 拿概念+公理+证据，覆盖外如实说 unavailable' },
    { title: '⑤ 问答验证', hint: '「本体问答」问"银行汇票和支票的区别"——本体口径的互斥公理会置顶并被引用', path: '/projects' },
    { title: '⑥ 进化对比', hint: '「时间轴 → 进化」：轨迹攒够触发 → 假设候选 → 配对评估通过才发布，进化不改坏系统' },
];

const DemoGuide: React.FC = () => {
    const [on, setOn] = useState(false);
    const [collapsed, setCollapsed] = useState(false);

    useEffect(() => {
        const sync = () => setOn(localStorage.getItem('demo_mode') === '1');
        sync();
        window.addEventListener('demo-mode-changed', sync);
        return () => window.removeEventListener('demo-mode-changed', sync);
    }, []);

    if (!on) return null;
    return (
        <div className="fixed z-[1000] right-4 bottom-4 w-[340px]">
            <Card size="small" title={
                <span><PlayCircleOutlined className="text-blue-500 mr-1" />演示模式 · 六步脚本</span>
            } extra={
                <Button size="small" type="text" icon={<CloseOutlined />}
                        onClick={() => setCollapsed(v => !v)} />
            }>
                {!collapsed ? (
                    <ol className="space-y-2 text-xs m-0 pl-4 list-decimal">
                        {STEPS.map((s) => (
                            <li key={s.title}>
                                <div className="font-medium">{s.title}
                                    {s.path && (
                                        <Button size="small" type="link" className="!px-1 !text-[11px]"
                                                onClick={() => { window.location.href = s.path!; }}>
                                            前往
                                        </Button>
                                    )}
                                </div>
                                <div className="text-gray-500">{s.hint}</div>
                            </li>
                        ))}
                    </ol>
                ) : (
                    <Tag color="blue" className="!m-0">六步演示进行中，点 ^ 展开</Tag>
                )}
            </Card>
        </div>
    );
};

export default DemoGuide;

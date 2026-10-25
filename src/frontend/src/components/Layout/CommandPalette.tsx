// components/Layout/CommandPalette.tsx - 全局命令面板（FE8，docs/16）
// Ctrl+K / Cmd+K 唤起：页面导航 + 我的项目直达 + 快捷动作。键盘流无门槛。
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Modal, Input } from 'antd';
import {
    SearchOutlined, HomeOutlined, FolderOpenOutlined, AuditOutlined,
    DatabaseOutlined, QuestionCircleOutlined, ToolOutlined,
} from '@ant-design/icons';
import { projectsApi } from '../../api/projects';

interface Item { key: string; label: string; hint?: string; icon: React.ReactNode; action: (nav: ReturnType<typeof useNavigate>) => void; }

const PAGE_ITEMS: Item[] = [
    { key: 'p-home', label: '工作台', hint: '首页统计', icon: <HomeOutlined />, action: n => n('/') },
    { key: 'p-projects', label: '我的项目', hint: '项目列表', icon: <FolderOpenOutlined />, action: n => n('/my-projects') },
    { key: 'p-reviews', label: '审核工作台', hint: '认证与裁决', icon: <AuditOutlined />, action: n => n('/reviews') },
    { key: 'p-assets', label: '资产中心', hint: '已发布本体', icon: <DatabaseOutlined />, action: n => n('/assets') },
    { key: 'p-qa', label: '本体问答', hint: 'GraphRAG 问答', icon: <QuestionCircleOutlined />, action: n => n('/qa') },
    { key: 'p-mcp', label: 'MCP 控制台', hint: '令牌与工具', icon: <ToolOutlined />, action: n => n('/tools/mcp') },
];

const CommandPalette: React.FC = () => {
    const [open, setOpen] = useState(false);
    const [q, setQ] = useState('');
    const [projects, setProjects] = useState<{ id: number; name: string }[]>([]);
    const [active, setActive] = useState(0);
    const navigate = useNavigate();
    const listRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
                e.preventDefault();
                setOpen(v => !v);
            }
            if (e.key === 'Escape') setOpen(false);
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, []);

    useEffect(() => {
        if (open && projects.length === 0) {
            projectsApi.getMyProjects().then(ps =>
                setProjects(ps.map(p => ({ id: p.id, name: p.name })))).catch(() => {});
        }
    }, [open, projects.length]);

    const items = useMemo<Item[]>(() => {
        const projItems: Item[] = projects.map(p => ({
            key: `proj-${p.id}`, label: p.name, hint: `项目 #${p.id} · 构建`,
            icon: <FolderOpenOutlined />,
            action: n => n(`/projects/${p.id}/documents`),
        }));
        const all = [...PAGE_ITEMS, ...projItems];
        if (!q.trim()) return all.slice(0, 10);
        const k = q.trim().toLowerCase();
        return all.filter(it => it.label.toLowerCase().includes(k)
            || (it.hint || '').toLowerCase().includes(k)).slice(0, 10);
    }, [q, projects]);

    useEffect(() => { setActive(0); }, [q]);

    const run = (it?: Item) => {
        const target = it || items[active];
        if (!target) return;
        setOpen(false);
        setQ('');
        target.action(navigate);
    };

    return (
        <Modal open={open} onCancel={() => setOpen(false)} footer={null} width={520
        } styles={{ body: { padding: 0 } }} closable={false}
               title={<span className="text-sm text-gray-500"><SearchOutlined /> 全局命令面板 — 输入页面或项目名，↑↓ 选择，回车前往</span>}>
            <div className="p-2">
                <Input autoFocus allowClear prefix={<SearchOutlined className="text-gray-300" />}
                       placeholder="页面 / 项目名…" value={q}
                       onChange={e => setQ(e.target.value)}
                       onKeyDown={e => {
                           if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(i + 1, items.length - 1)); }
                           if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(i - 1, 0)); }
                           if (e.key === 'Enter') { e.preventDefault(); run(); }
                       }} />
                <div ref={listRef} className="mt-2 max-h-80 overflow-auto">
                    {items.map((it, i) => (
                        <div key={it.key}
                             onMouseEnter={() => setActive(i)}
                             onClick={() => run(it)}
                             className={`flex items-center gap-2 px-3 py-2 rounded cursor-pointer text-sm ${
                                 i === active ? 'bg-blue-50 text-blue-700' : 'text-gray-700'}`}>
                            <span className="text-gray-400">{it.icon}</span>
                            <span className="flex-1 truncate">{it.label}</span>
                            {it.hint && <span className="text-xs text-gray-400">{it.hint}</span>}
                        </div>
                    ))}
                    {items.length === 0 && (
                        <div className="text-center text-xs text-gray-400 py-8">没有匹配项</div>
                    )}
                </div>
            </div>
        </Modal>
    );
};

export default CommandPalette;

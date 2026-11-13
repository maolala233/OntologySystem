// src/pages/McpConsolePage.tsx - MCP 服务控制台（R8，布局对齐 MCP Inspector v0.20.0）
// 左栏连接配置（端点 + sk-mcp- 令牌签发/选择/撤销 + 连接状态 + 服务端信息卡）
// │ 顶部 Tabs（工具 / 资源 / Ping / 接入说明）│ 工具清单（搜索 + 刷新/清空）
// │ 工具详情（参数卡 + Run Tool + 彩色 JSON 树结果）
// │ 底部编号调用历史（可展开请求/响应）+ 服务端通知占位。全部走平台 POST /mcp 网关。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Empty, Input, InputNumber, Modal, Select, Tabs, Tag, Tooltip } from 'antd';
import { message } from '../shared/feedback';
import {
    ApiOutlined, BookOutlined, CaretRightOutlined, CheckCircleFilled, ClearOutlined, CloseCircleFilled,
    CopyOutlined, DeleteOutlined, DisconnectOutlined, FolderOpenOutlined,
    LinkOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, RightOutlined,
    SearchOutlined, ThunderboltOutlined, ToolOutlined, UndoOutlined, WifiOutlined,
} from '@ant-design/icons';
import {
    McpRpcError, createMcpToken, createMyMcpToken, getDefaultMcpEndpoint,
    getMcpEndpoint, listMcpTokens, listMyMcpTokens,
    mcpRpc, revokeMcpToken, revokeMyMcpToken, textFromCallResult,
    MCP_ENDPOINT_KEY,
    type McpTokenItem, type McpToolSpec,
} from '../api/mcp';
import { adminApi } from '../api/admin';
import { projectsApi } from '../api/projects';
import { useAuthStore } from '../shared/auth/authStore';
import type { AdminUser } from '../api/admin';
import type { ProjectData } from '../types/ontology';
import Navbar from '../components/Layout/Navbar';

// ───────────────────────── JSON 彩色折叠树（对齐 Inspector 的 JSON Viewer）

function JsonLeaf({ value }: { value: unknown }) {
    if (typeof value === 'string') return <span className="text-emerald-600 break-all">"{value}"</span>;
    if (typeof value === 'number') return <span className="text-blue-600">{String(value)}</span>;
    if (typeof value === 'boolean') return <span className="text-[#B8790F]">{String(value)}</span>;
    if (value === null || value === undefined) return <span className="text-[#66738F] italic">null</span>;
    return <span className="text-[#52607A]">{String(value)}</span>;
}

function JsonNode({ name, value, depth = 0 }: { name?: string; value: unknown; depth?: number }) {
    const [open, setOpen] = useState(depth < 4);
    const isObj = value !== null && typeof value === 'object';
    if (!isObj) {
        return (
            <div className="leading-6">
                {name !== undefined && <span className="text-[#3D4B66]">{name}: </span>}
                <JsonLeaf value={value} />
            </div>
        );
    }
    const entries = Array.isArray(value)
        ? value.map((v, i) => [String(i), v] as const)
        : Object.entries(value as Record<string, unknown>);
    const [openB, closeB] = Array.isArray(value) ? ['[', ']'] as const : ['{', '}'] as const;
    return (
        <div className="leading-6">
            <button type="button" onClick={() => setOpen(o => !o)}
                className="inline-flex items-center gap-1 text-left hover:bg-[#F7F9FD] rounded px-0.5 -ml-0.5">
                <CaretRightOutlined className={`text-[10px] text-[#66738F] transition-transform ${open ? 'rotate-90' : ''}`} />
                {name !== undefined && <span className="text-[#1F2A44]">{name}</span>}
                <span className="text-[#66738F]">{openB}</span>
                {!open && (
                    <span className="text-[#66738F] text-xs">
                        {entries.length} 项 <span>{closeB}</span>
                    </span>
                )}
            </button>
            {open && (
                <>
                    <div className="ml-3 border-l border-[#F0F4FB] pl-3">
                        {entries.map(([k, v]) => <JsonNode key={k} name={k} value={v} depth={depth + 1} />)}
                    </div>
                    <div className="text-[#66738F]">{closeB}</div>
                </>
            )}
        </div>
    );
}

/** 结果体：文本能解析为 JSON 时渲染彩色树，否则渲染纯文本；带复制按钮 */
function ResultBody({ text }: { text: string }) {
    const parsed = useMemo(() => { try { return JSON.parse(text); } catch { return null; } }, [text]);
    const [copied, setCopied] = useState(false);
    return (
        <div className="relative border border-[#E7EEF9] rounded-lg bg-white">
            <Tooltip title="复制结果">
                <Button size="small" type="text" icon={<CopyOutlined />}
                    className="!absolute top-1.5 right-1.5 z-10"
                    onClick={() => {
                        navigator.clipboard.writeText(text);
                        setCopied(true);
                        setTimeout(() => setCopied(false), 1500);
                    }} />
            </Tooltip>
            <div className="p-3 overflow-auto max-h-96 font-mono text-[12px]">
                {parsed !== null ? <JsonNode value={parsed} /> : <pre className="whitespace-pre-wrap">{text}</pre>}
            </div>
            {copied && <span className="absolute bottom-1.5 right-2 text-[11px] text-[#66738F]">已复制</span>}
        </div>
    );
}

// ───────────────────────── 页面

type TabKey = 'tools' | 'resources' | 'ping' | 'docs';

/** 去掉模板字符串因源码缩进产生的公共前导空格（接入说明里有大段多行代码）：
 *  首行通常顶格书写，其余行带 JSX 源码缩进，因此按「除首行外」的最小缩进统一回退 */
const dedent = (code: string) => {
    const lines = code.replace(/^\n/, '').replace(/\n\s*$/, '').split('\n');
    const body = lines.slice(1).filter(l => l.trim());
    const indents = body.map(l => (l.match(/^ +/) || [''])[0].length);
    const pad = indents.length ? Math.min(...indents) : 0;
    return [lines[0] ?? '', ...lines.slice(1).map(l => l.slice(pad))].join('\n');
};

/** 接入说明用的代码块：深色底 + 顶部标题栏 + 复制 */
function CodeBlock({ title, code }: { title?: string; code: string }) {
    const [copied, setCopied] = useState(false);
    const text = useMemo(() => dedent(code), [code]);
    return (
        <div className="rounded-xl overflow-hidden border border-[#1A2233] bg-[#0F1420]">
            <div className="flex items-center justify-between gap-2 px-3 py-1.5 border-b border-white/10">
                <span className="text-[11px] text-[#8A94B8]">{title}</span>
                <Tooltip title="复制代码">
                    <Button size="small" type="text" className="!text-[#8A94AB] hover:!text-white"
                        icon={copied ? <CheckCircleFilled className="text-emerald-400" /> : <CopyOutlined />}
                        onClick={() => {
                            navigator.clipboard.writeText(text);
                            setCopied(true);
                            setTimeout(() => setCopied(false), 1500);
                        }} />
                </Tooltip>
            </div>
            <pre className="m-0 p-3 font-mono text-[12px] leading-relaxed text-gray-100 overflow-auto max-h-80 whitespace-pre">{text}</pre>
        </div>
    );
}

interface McpResourceSpec {
    uri: string;
    name?: string;
    description?: string;
    mimeType?: string;
}

interface HistoryEntry {
    id: number;
    seq: number;
    method: string;
    ok: boolean;
    at: string;
    request: Record<string, unknown>;
    response: Record<string, unknown> | null; // result 或 error 包络
}

let historySeq = 0;

export default function McpConsolePage() {
    // ── 角色：admin 走 /api/admin/mcp-tokens（可代他人签发）；普通用户走 /api/mcp-tokens 自助端（限本人）
    const currentUser = useAuthStore(s => s.user);
    const isAdmin = currentUser?.role === 'admin';

    // ── 连接配置
    const [tokens, setTokens] = useState<McpTokenItem[]>([]);
    const [selectedTokenId, setSelectedTokenId] = useState<number | null>(null);
    const bearer = useMemo(
        () => (selectedTokenId != null ? localStorage.getItem(`mcp_plain_${selectedTokenId}`) ?? '' : ''),
        [selectedTokenId],
    );
    const [connected, setConnected] = useState(false);
    const [serverInfo, setServerInfo] = useState<{ name?: string; version?: string } | null>(null);
    const [protocolVersion, setProtocolVersion] = useState<string | null>(null);

    // ── Tab 与数据
    const [activeTab, setActiveTab] = useState<TabKey>('tools');
    const [tools, setTools] = useState<McpToolSpec[]>([]);
    const [toolSearch, setToolSearch] = useState('');
    const [selectedTool, setSelectedTool] = useState<McpToolSpec | null>(null);
    const [formValues, setFormValues] = useState<Record<string, string>>({});
    const [calling, setCalling] = useState(false);
    const [callResult, setCallResult] = useState<{ ok: boolean; text: string } | null>(null);

    const [resources, setResources] = useState<McpResourceSpec[]>([]);
    const [selectedResource, setSelectedResource] = useState<McpResourceSpec | null>(null);
    const [resourceText, setResourceText] = useState<{ ok: boolean; text: string } | null>(null);
    const [readingRes, setReadingRes] = useState(false);

    const [pinging, setPinging] = useState(false);
    const [pingResult, setPingResult] = useState<{ ok: boolean; ms?: number; msg?: string } | null>(null);

    const [history, setHistory] = useState<HistoryEntry[]>([]);
    const [expandedHistory, setExpandedHistory] = useState<number | null>(null);
    const seqRef = useRef(0);

    // ── 签发令牌
    const [issueOpen, setIssueOpen] = useState(false);
    const [users, setUsers] = useState<AdminUser[]>([]);
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [issueForm, setIssueForm] = useState({ user_id: null as number | null, project_id: null as number | null, name: '', can_write: false, expires_days: 30 });
    const [issuedPlain, setIssuedPlain] = useState<string | null>(null);

    // 端点：默认按页面地址推导（host 随访问 IP/域名）；容器化/反代部署可在输入框改一次，
    // 存 localStorage 后所有调用与文档示例联动；清空输入框恢复默认
    const defaultEndpoint = useMemo(() => getDefaultMcpEndpoint(), []);
    const [endpoint, setEndpoint] = useState<string>(() => getMcpEndpoint());
    const endpointDirty = endpoint.trim() !== defaultEndpoint;

    /** 记录一次 JSON-RPC 调用（带请求/响应包络，可展开回放） */
    const pushHistory = useCallback((method: string, request: Record<string, unknown>, ok: boolean, response: Record<string, unknown> | null) => {
        seqRef.current += 1;
        setHistory(prev => [{
            id: Date.now() + Math.random(), seq: seqRef.current,
            method, ok, at: new Date().toLocaleTimeString(),
            request, response,
        }, ...prev].slice(0, 50));
    }, []);

    const loadTokens = useCallback(async () => {
        try {
            const items = isAdmin ? await listMcpTokens() : await listMyMcpTokens();
            setTokens(items.filter(t => !t.revoked_at));
            setSelectedTokenId(prev => prev ?? items.find(t => !t.revoked_at)?.id ?? null);
        } catch {
            message.error(isAdmin ? '加载 MCP 令牌失败（需 admin）' : '加载 MCP 令牌失败');
        }
    }, [isAdmin]);

    useEffect(() => {
        loadTokens();
        (async () => {
            try {
                if (isAdmin) setUsers(await adminApi.listUsers()); // 仅 admin 需要持有人下拉
                // 自助签发要求项目成员身份（后端 _require_project_member）：非 admin 只列自己的项目，
                // 否则选了公共资产后点签发会 403「仅项目成员可为该项目签发令牌」；admin 走管理端接口不受限
                setProjects(isAdmin
                    ? await projectsApi.getSelectableProjects()
                    : await projectsApi.getMyProjects());
            } catch { /* 下拉留空 */ }
        })();
    }, [loadTokens, isAdmin]);

    const disconnect = useCallback(() => {
        setConnected(false);
        setServerInfo(null);
        setProtocolVersion(null);
        setTools([]);
        setSelectedTool(null);
        setCallResult(null);
        setResources([]);
        setSelectedResource(null);
        setResourceText(null);
        setPingResult(null);
    }, []);

    // 改端点即断开当前连接（避免还挂在旧端点的会话状态上）
    const changeEndpoint = useCallback((v: string) => {
        setEndpoint(v);
        const t = v.trim();
        if (t) localStorage.setItem(MCP_ENDPOINT_KEY, t);
        else localStorage.removeItem(MCP_ENDPOINT_KEY);
        disconnect();
    }, [disconnect]);

    /** 调用 JSON-RPC 并自动写历史；返回 result 或抛 McpRpcError */
    const rpc = useCallback(async (method: string, params: Record<string, unknown> = {}) => {
        historySeq += 1;
        const request = { jsonrpc: '2.0', id: historySeq, method, params };
        try {
            const result = await mcpRpc(bearer, method, params, endpoint.trim() || undefined);
            pushHistory(method, request, true, { jsonrpc: '2.0', id: historySeq, result });
            return result;
        } catch (e) {
            const err = e instanceof McpRpcError
                ? { code: e.code, message: e.message, data: e.data }
                : { code: -1, message: String(e) };
            pushHistory(method, request, false, { jsonrpc: '2.0', id: historySeq, error: err });
            throw e;
        }
    }, [bearer, endpoint, pushHistory]);

    // 连接 = initialize（取 serverInfo/协议版本）→ tools/list
    const connect = useCallback(async () => {
        if (!bearer) {
            message.warning('请先选择或签发令牌（明文仅签发时可见，已存本页内存）');
            return;
        }
        disconnect();
        try {
            const init = await rpc('initialize', { protocolVersion: '2024-11-05', capabilities: {} });
            const info = (init?.serverInfo ?? {}) as { name?: string; version?: string };
            setServerInfo(info);
            setProtocolVersion((init?.protocolVersion as string) ?? null);
            const result = await rpc('tools/list');
            const list = (result?.tools as McpToolSpec[]) ?? [];
            setTools(list);
            setConnected(true);
            message.success(`已连接：${list.length} 个工具`);
        } catch (e) {
            setConnected(false);
            const msg = e instanceof McpRpcError ? `RPC ${e.code}: ${e.message}` : String(e);
            message.error(`连接失败：${msg}`);
        }
    }, [bearer, rpc, disconnect]);

    const listTools = useCallback(async () => {
        try {
            const result = await rpc('tools/list');
            const list = (result?.tools as McpToolSpec[]) ?? [];
            setTools(list);
            message.success(`已刷新：${list.length} 个工具`);
        } catch (e) {
            message.error(e instanceof McpRpcError ? e.message : String(e));
        }
    }, [rpc]);

    const runTool = useCallback(async () => {
        if (!selectedTool || !bearer) return;
        setCalling(true);
        setCallResult(null);
        try {
            const args: Record<string, unknown> = {};
            for (const [k, v] of Object.entries(formValues)) {
                if (v === '' || v == null) continue;
                // array/object 入参按 JSON 解析，解析失败按原样传（由网关报参数错误）
                const propType = selectedTool.inputSchema?.properties?.[k]?.type;
                if (propType === 'array' || propType === 'object') {
                    try { args[k] = JSON.parse(v); continue; } catch { /* 原样传 */ }
                }
                args[k] = v;
            }
            const result = await rpc('tools/call', { name: selectedTool.name, arguments: args });
            setCallResult({ ok: true, text: textFromCallResult(result) });
        } catch (e) {
            const msg = e instanceof McpRpcError ? `${e.message}（RPC ${e.code}）` : String(e);
            setCallResult({ ok: false, text: msg });
        } finally {
            setCalling(false);
        }
    }, [selectedTool, bearer, formValues, rpc]);

    const listResources = useCallback(async () => {
        try {
            const result = await rpc('resources/list');
            setResources(((result?.resources as McpResourceSpec[]) ?? []));
        } catch (e) {
            message.error(e instanceof McpRpcError ? e.message : String(e));
        }
    }, [rpc]);

    const readResource = useCallback(async (res: McpResourceSpec) => {
        setSelectedResource(res);
        setResourceText(null);
        setReadingRes(true);
        try {
            const result = await rpc('resources/read', { uri: res.uri });
            const contents = (result?.contents as { text?: string }[]) ?? [];
            setResourceText({ ok: true, text: contents.map(c => c.text ?? '').join('\n') || JSON.stringify(result, null, 2) });
        } catch (e) {
            const msg = e instanceof McpRpcError ? `${e.message}（RPC ${e.code}）` : String(e);
            setResourceText({ ok: false, text: msg });
        } finally {
            setReadingRes(false);
        }
    }, [rpc]);

    const doPing = useCallback(async () => {
        setPinging(true);
        setPingResult(null);
        const t0 = performance.now();
        try {
            await rpc('ping');
            setPingResult({ ok: true, ms: Math.round(performance.now() - t0) });
        } catch (e) {
            setPingResult({ ok: false, msg: e instanceof McpRpcError ? e.message : String(e) });
        } finally {
            setPinging(false);
        }
    }, [rpc]);

    const issue = useCallback(async () => {
        const holderId = isAdmin ? issueForm.user_id : currentUser?.id ?? null;
        if (holderId == null || issueForm.project_id == null || !issueForm.name.trim()) {
            message.warning(isAdmin ? '请填写持有人、绑定项目与令牌名称' : '请填写绑定项目与令牌名称');
            return;
        }
        try {
            const created = isAdmin
                ? await createMcpToken({
                    user_id: holderId, project_id: issueForm.project_id,
                    name: issueForm.name.trim(), can_write: issueForm.can_write,
                    expires_days: issueForm.expires_days || null,
                })
                : await createMyMcpToken({
                    project_id: issueForm.project_id,
                    name: issueForm.name.trim(), can_write: issueForm.can_write,
                    expires_days: issueForm.expires_days || null,
                });
            localStorage.setItem(`mcp_plain_${created.id}`, created.token); // 仅本页内存，明文不落库
            setIssuedPlain(created.token);
            await loadTokens();
            setSelectedTokenId(created.id);
        } catch (e: any) {
            message.error(e?.response?.data?.error?.message || '签发失败');
        }
    }, [isAdmin, currentUser, issueForm, loadTokens]);

    const doRevoke = useCallback(async (id: number) => {
        try {
            if (isAdmin) await revokeMcpToken(id); else await revokeMyMcpToken(id);
            localStorage.removeItem(`mcp_plain_${id}`);
            if (selectedTokenId === id) { setSelectedTokenId(null); disconnect(); }
            loadTokens();
        } catch {
            message.error('撤销失败');
        }
    }, [isAdmin, selectedTokenId, loadTokens, disconnect]);

    const schemaProps = Object.entries(selectedTool?.inputSchema?.properties ?? {});
    const requiredKeys = selectedTool?.inputSchema?.required ?? [];
    const filteredTools = toolSearch.trim()
        ? tools.filter(t => `${t.name} ${t.description ?? ''}`.toLowerCase().includes(toolSearch.trim().toLowerCase()))
        : tools;
    const selectedToken = tokens.find(t => t.id === selectedTokenId);

    // Copy Input：把当前表单值按 tools/call 的 arguments 复制为 JSON
    const copyInput = useCallback(() => {
        if (!selectedTool) return;
        const args: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(formValues)) {
            if (v === '' || v == null) continue;
            const propType = selectedTool.inputSchema?.properties?.[k]?.type;
            if (propType === 'array' || propType === 'object') {
                try { args[k] = JSON.parse(v); continue; } catch { /* 原样 */ }
            }
            args[k] = v;
        }
        navigator.clipboard.writeText(JSON.stringify({ name: selectedTool.name, arguments: args }, null, 2));
        message.success('已复制调用入参 JSON');
    }, [selectedTool, formValues]);

    // 顶部导航：与全站 Tabs 统一（下划线 + 品牌色 ink bar），图标 + 数量弱提示
    const tabItems = [
        { key: 'tools' as TabKey, icon: <ToolOutlined />, label: '工具', count: connected ? tools.length : undefined },
        { key: 'resources' as TabKey, icon: <FolderOpenOutlined />, label: '资源', count: connected ? resources.length : undefined },
        { key: 'ping' as TabKey, icon: <WifiOutlined />, label: 'Ping' },
        { key: 'docs' as TabKey, icon: <BookOutlined />, label: '接入说明' },
    ];

    // 接入说明示例中的令牌：已选令牌且有明文缓存则代入，否则占位
    const docTok = bearer || 'sk-mcp-<你的令牌>';
    // 文档示例统一用生效端点（输入框清空时回落默认）
    const effEndpoint = endpoint.trim() || defaultEndpoint;

    return (
        <div className="h-full flex flex-col app-canvas mcp-surface">
            <style>{`
                /* 内容卡片比外壳实、但不做实心白（2026-11-13）：
                   0.96 → 0.90，让底图的冷蓝透进来一丝，卡和外壳之间不再是一堵白墙。
                   再往下压（0.7 以下）卡片就和画布同色了，输入框、表单项会失去依托 */
                .mcp-surface .surface-card {
                    background: linear-gradient(180deg, rgba(255, 255, 255, 0.90) 0%, rgba(250, 252, 255, 0.86) 100%);
                }
                /* 标签压薄（2026-11-13）：默认 Tag 是实心 #F1F4FA，一屏十几个小块
                   叠在白卡上会显出一层层的"白补丁" */
                .mcp-surface .ant-tag {
                    background: rgba(255, 255, 255, 0.52) !important;
                    border-color: rgba(219, 228, 242, 0.70) !important;
                }
            `}</style>
            <Navbar breadcrumbs={[{ title: '首页', path: '/' }, { title: '工具' }, { title: 'MCP 服务' }]} />
            <div className="flex flex-1 min-h-0">
                {/* ══ 左栏：连接配置 */}
                <aside className="w-[260px] shrink-0 bg-white/30 border-r border-[#E4EBF7]/70 flex flex-col min-h-0">
                    {/* 标题 + 连接状态 */}
                    <div className="px-4 py-3 border-b border-[#F0F4FB] flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                            <span className="w-8 h-8 shrink-0 rounded-lg bg-[rgba(91,141,239,0.10)] text-[#5B8DEF] flex items-center justify-center">
                                <ApiOutlined />
                            </span>
                            <div className="min-w-0">
                                <div className="font-semibold text-[#1F2A44] text-sm leading-tight">MCP 控制台</div>
                                <div className="text-[11px] text-[#8A94AB] leading-tight mt-0.5">连接与令牌</div>
                            </div>
                        </div>
                        <span
                            className="inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-[3px] text-[11px] leading-none"
                            style={connected
                                ? { background: 'rgba(91, 141, 239, 0.10)', color: '#2F62D8' }
                                : { background: 'rgba(15, 23, 42, 0.05)', color: '#8A94AB' }}
                        >
                            <span
                                className="h-1.5 w-1.5 rounded-full"
                                style={{ background: connected ? '#2F62D8' : '#B7C1D4' }}
                            />
                            {connected ? '已连接' : '未连接'}
                        </span>
                    </div>

                    <div className="flex-1 overflow-auto p-4 space-y-4">
                        {/* 端点 */}
                        <section>
                            <div className="flex items-center justify-between mb-1.5">
                                <span className="text-[12px] font-medium text-[#8A94AB]">端点</span>
                                <Tag className="!m-0 !text-[10.5px]">HTTP Streamable</Tag>
                            </div>
                            <div className="flex gap-1.5">
                                <Tooltip title="默认按页面地址自动推导；容器化/反向代理部署可直接改为实际可达地址（如 https://域名/mcp）">
                                    <Input size="small" value={endpoint}
                                        onChange={e => changeEndpoint(e.target.value)}
                                        placeholder={defaultEndpoint}
                                        className="!text-[11px] font-mono" />
                                </Tooltip>
                                {endpointDirty && (
                                    <Tooltip title="恢复默认端点">
                                        <Button size="small" type="text" icon={<UndoOutlined />}
                                            onClick={() => changeEndpoint(defaultEndpoint)} />
                                    </Tooltip>
                                )}
                                <Tooltip title="复制端点">
                                    <Button size="small" type="text" icon={<CopyOutlined />}
                                        onClick={() => { navigator.clipboard.writeText(endpoint.trim() || defaultEndpoint); message.success('已复制'); }} />
                                </Tooltip>
                            </div>
                            <div className="text-[11px] text-[#8A94AB] mt-1">无状态 POST · 每次请求独立</div>
                        </section>

                        {/* 令牌 */}
                        <section>
                            <div className="flex items-center justify-between mb-1.5">
                                <span className="text-[12px] font-medium text-[#8A94AB]">sk-mcp- 令牌</span>
                                <Button size="small" type="link" className="!px-0 !text-[11px]" icon={<PlusOutlined />}
                                    onClick={() => { setIssuedPlain(null); setIssueOpen(true); }}>签发</Button>
                            </div>
                            <Select
                                value={selectedTokenId ?? undefined}
                                onChange={v => { setSelectedTokenId(v); disconnect(); }}
                                placeholder="选择令牌"
                                className="w-full"
                                optionLabelProp="label"
                                options={tokens.map(t => ({
                                    value: t.id,
                                    label: `${t.name}（${t.token_hint}）`,
                                }))}
                                notFoundContent={<span className="text-xs text-[#66738F]">暂无令牌，点击「签发」创建</span>}
                            />
                            {selectedToken && (
                                <div className="mt-2 rounded-lg border border-[#F0F4FB] bg-[#F7F9FD] px-2.5 py-2 space-y-1">
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="flex items-center gap-1.5 min-w-0">
                                            <span
                                                className="shrink-0 rounded-full px-1.5 py-[2px] text-[10px] leading-none"
                                                style={selectedToken.can_write
                                                    ? { background: 'rgba(242, 185, 80, 0.20)', color: '#B8790F' }
                                                    : { background: 'rgba(91, 141, 239, 0.12)', color: '#2A57C6' }}
                                            >
                                                {selectedToken.can_write ? '读写' : '只读'}
                                            </span>
                                            <span className="text-[11px] text-[#66738F] truncate">项目 #{selectedToken.project_id}</span>
                                        </span>
                                        <Tooltip title="撤销该令牌">
                                            <Button size="small" type="text" danger icon={<DeleteOutlined />}
                                                className="!px-1" onClick={() => doRevoke(selectedToken.id)} />
                                        </Tooltip>
                                    </div>
                                    {selectedToken.expires_at && (
                                        <div className="text-[11px] text-[#8A94AB]">有效期至 {selectedToken.expires_at.slice(0, 10)}</div>
                                    )}
                                </div>
                            )}
                        </section>

                        {/* 连接操作 */}
                        <section className="pt-1">
                            <div className="flex gap-2">
                                {!connected ? (
                                    <Button type="primary" block icon={<PlayCircleOutlined />} onClick={connect} disabled={!bearer}>
                                        连接
                                    </Button>
                                ) : (
                                    <>
                                        <Button block icon={<ReloadOutlined />} onClick={connect}>重连</Button>
                                        <Button
                                            block
                                            icon={<DisconnectOutlined />}
                                            onClick={disconnect}
                                            className="!text-[#B42318] hover:!border-[#F2C6CE] hover:!bg-[#FDF2F4] hover:!text-[#B42318]"
                                        >
                                            断开
                                        </Button>
                                    </>
                                )}
                            </div>
                            {!bearer && (
                                <div className="mt-1.5 text-[11px] text-[#8A94AB]">先选择或签发令牌后才能连接</div>
                            )}
                        </section>

                        {/* 服务端信息卡（连接后展示） */}
                        {connected && serverInfo && (
                            <section className="rounded-xl border border-[#DCE7F7] p-3" style={{ background: 'rgba(91, 141, 239, 0.06)' }}>
                                <div className="flex items-center gap-1.5 min-w-0">
                                    <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: '#2F62D8' }} />
                                    <span className="font-mono text-sm text-[#1F2A44] truncate">{serverInfo.name ?? 'mcp-server'}</span>
                                </div>
                                <div className="text-[11px] text-[#66738F] mt-1">
                                    Version {serverInfo.version ?? '-'}
                                    {protocolVersion && ` · 协议 ${protocolVersion}`}
                                </div>
                            </section>
                        )}
                    </div>

                    <div className="border-t border-[#F0F4FB] px-4 py-3 text-[11px] text-[#8A94AB] leading-relaxed">
                        明文令牌仅签发时展示一次（本页内存留存）；对外服务请把
                        <span className="font-mono"> Authorization: Bearer sk-mcp-* </span>
                        配入任意支持 Streamable HTTP 的 MCP 客户端。
                    </div>
                </aside>

                {/* ══ 右侧主区 */}
                <main className="flex-1 min-w-0 flex flex-col min-h-0">
                    {/* 顶部导航（与全站 Tabs 同款下划线样式） */}
                    <div className="shrink-0 bg-white/30 border-b border-[#E4EBF7]/70 px-4">
                        <Tabs
                            activeKey={activeTab}
                            onChange={key => setActiveTab(key as TabKey)}
                            items={tabItems.map(t => ({
                                key: t.key,
                                label: (
                                    <span className="inline-flex items-center gap-1.5">
                                        {t.icon}
                                        <span>{t.label}</span>
                                        {t.count !== undefined && (
                                            <span className="text-[11px] text-[#8A94AB]">{t.count}</span>
                                        )}
                                    </span>
                                ),
                            }))}
                            className="mcp-nav-tabs"
                        />
                    </div>

                    {/* 内容区 */}
                    <div className="flex-1 min-h-0 flex">
                        {activeTab === 'docs' && (
                            <div className="flex-1 overflow-auto scrollbar-stable p-4 sm:p-6">
                                <div className="max-w-3xl mx-auto space-y-4 pb-8">
                                    {/* 当前配置速览 */}
                                    <div className="surface-card p-4">
                                        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                                            <Tag className="!m-0 !text-[10.5px]">HTTP Streamable</Tag>
                                            <span className="font-mono text-xs text-[#52607A] break-all">{effEndpoint}</span>
                                            <span className="text-[#DCE3F0]">|</span>
                                            <span className="text-xs text-[#66738F]">
                                                {selectedToken
                                                    ? `当前令牌：${selectedToken.name}（${selectedToken.token_hint}）`
                                                    : '尚未选择令牌，示例中用占位符表示'}
                                            </span>
                                        </div>
                                    </div>

                                    <div className="surface-card p-5">
                                        <div className="text-sm font-semibold text-[#1F2A44] mb-3">三步接入</div>
                                        <ol className="text-sm text-[#52607A] list-decimal pl-5 space-y-1.5 leading-relaxed">
                                            <li>左侧「签发」创建 <span className="font-mono">sk-mcp-</span> 令牌：{isAdmin ? '选持有人、' : ''}绑定项目、勾是否允许写入——<b>明文只展示一次</b>，当场复制</li>
                                            <li>端点默认为 <span className="font-mono">{effEndpoint}</span>（host 随访问地址自动推导；容器化/反代部署可在左栏改，HTTP Streamable，无状态 POST-only）</li>
                                            <li>把端点 + 令牌配到下方任意一种客户端 / 自己的 agent 里</li>
                                        </ol>
                                        <div className="mt-3 text-xs text-[#66738F] leading-relaxed">
                                            令牌绑定单项目（数据隔离）；只读令牌不暴露写入工具；限流：读 60 次/分、写 20 次/分。
                                            {bearer ? ' 已检测到当前选中令牌，下方示例已代入。' : ' 尚未选令牌，示例中用占位符表示，替换为你的明文即可。'}
                                        </div>
                                    </div>

                                    <div className="surface-card p-5 space-y-3">
                                        <div className="text-sm font-semibold text-[#1F2A44]">curl 调用</div>
                                        <CodeBlock title="① 握手 + 列出工具（tools/list）" code={`# initialize（唯一不需要令牌的方法）
    curl -s -X POST ${effEndpoint} -H "Content-Type: application/json" \\
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}'

    # 列出全部工具（需令牌）
    curl -s -X POST ${effEndpoint} \\
      -H "Content-Type: application/json" \\
      -H "Authorization: Bearer ${docTok}" \\
      -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'`} />
                                        <CodeBlock title="② 调用 ask（GraphRAG 问答）" code={`curl -s -X POST ${effEndpoint} \\
      -H "Content-Type: application/json" \\
      -H "Authorization: Bearer ${docTok}" \\
      -d '{
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
          "name": "ask",
          "arguments": { "question": "这个产品的投资范围是什么", "top_k": 12 }
        }
      }'`} />
                                        <CodeBlock title="③ 提取答案正文（jq，可选）" code={`# ask 返回双层 JSON：content[0].text 是字符串化的结果（answer + sources 溯源原文）
    # 人看只取 answer；agent 建议连同 sources 一起消费用于核对
    ... | jq -r '.result.content[0].text | fromjson | .answer'

    # 只看答案 + 置信度 + 引用来源摘要（不带原文大段）
    ... | jq '{answer: .answer, confidence: .confidence,
               sources: [.sources[] | {doc_file, score, ref_type}]}'`} />
                                    </div>

                                    <div className="surface-card p-5 space-y-3">
                                        <div className="text-sm font-semibold text-[#1F2A44]">Python 调用（官方 MCP SDK）</div>
                                        <CodeBlock title="pip install mcp" code={`import asyncio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    ENDPOINT = "${effEndpoint}"
    HEADERS = {"Authorization": "Bearer ${docTok}"}

    async def main():
        async with streamablehttp_client(ENDPOINT, headers=HEADERS) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                tools = await s.list_tools()
                print([t.name for t in tools.tools])

                # GraphRAG 问答
                res = await s.call_tool("ask", {"question": "这个产品的投资范围是什么"})
                payload = json.loads(res.content[0].text)
                print(payload["answer"])
                for src in payload["sources"]:
                    print(src["doc_file"], round(src["score"], 3))

    asyncio.run(main())`} />
                                    </div>

                                    <div className="surface-card p-5 space-y-3">
                                        <div className="text-sm font-semibold text-[#1F2A44]">配到现成 MCP 客户端</div>
                                        <CodeBlock title="Cursor 等支持 HTTP Streamable 的客户端（mcpServers JSON）" code={`{
      "mcpServers": {
        "ontology-platform": {
          "url": "${effEndpoint}",
          "headers": { "Authorization": "Bearer ${docTok}" }
        }
      }
    }`} />
                                        <CodeBlock title="Claude Code 命令行" code={`claude mcp add --transport http ontology-platform \\
      ${effEndpoint} \\
      --header "Authorization: Bearer ${docTok}"`} />
                                        <CodeBlock title="仅支持 stdio 的客户端（如桌面版 Claude）：mcp-remote 桥接" code={`{
      "mcpServers": {
        "ontology-platform": {
          "command": "npx",
          "args": ["-y", "mcp-remote", "${effEndpoint}",
                   "--header", "Authorization: Bearer ${docTok}"]
        }
      }
    }`} />
                                    </div>

                                    <div className="surface-card p-5">
                                        <div className="text-sm font-semibold text-[#1F2A44] mb-3">注意事项</div>
                                        <ul className="text-sm text-[#52607A] list-disc pl-5 space-y-1.5 leading-relaxed">
                                            <li>端点为无状态实现：每次 POST 独立、不需要会话保持；GET /mcp 返回 405 是预期行为</li>
                                            <li>除 <span className="font-mono">initialize</span> 外的所有方法都要 <span className="font-mono">Authorization: Bearer sk-mcp-*</span>；令牌无效返回 401 + JSON-RPC 认证错误</li>
                                            <li>令牌与单个项目绑定，工具的读写范围仅限该项目图谱；撤销后立即失效</li>
                                            <li>限流：读类 60 次/分钟、写类 20 次/分钟，超限返回限流错误，稍后重试</li>
                                            <li>ask 的响应较大（sources 含命中文档原文整段，用于溯源核对），curl 调试建议配合 jq 取 answer</li>
                                            <li>接入后建议 agent 先调 <span className="font-mono">get_ontology_schema</span> / <span className="font-mono">search_entities</span> 探索，再 <span className="font-mono">query_graph</span> / <span className="font-mono">ask</span> 深挖</li>
                                        </ul>
                                    </div>
                                </div>
                            </div>
                        )}
                        {activeTab === 'tools' && (
                            <>
                                {/* 工具清单 */}
                                <section className="w-80 shrink-0 bg-white/30 border-r border-[#E4EBF7]/70 flex flex-col min-h-0">
                                    <div className="px-4 py-3 border-b border-[#F0F4FB] flex items-center justify-between gap-2">
                                        <div className="flex items-center gap-2 min-w-0">
                                            <span className="text-sm font-semibold text-[#1F2A44]">工具清单</span>
                                            <Tag className="!m-0 !text-[10.5px]">{tools.length}</Tag>
                                        </div>
                                        <div className="flex items-center gap-0.5">
                                            <Tooltip title="刷新工具列表（List Tools）">
                                                <Button size="small" type="text" icon={<ReloadOutlined />}
                                                    disabled={!connected} onClick={listTools} />
                                            </Tooltip>
                                            <Tooltip title="清空工具列表">
                                                <Button size="small" type="text" icon={<ClearOutlined />}
                                                    disabled={tools.length === 0}
                                                    onClick={() => { setTools([]); setSelectedTool(null); setCallResult(null); }} />
                                            </Tooltip>
                                        </div>
                                    </div>
                                    <div className="px-4 py-2 border-b border-[#F0F4FB]">
                                        <Input size="small" allowClear prefix={<SearchOutlined className="text-[#A9B4C9]" />}
                                            placeholder="搜索工具名称或描述" value={toolSearch}
                                            onChange={e => setToolSearch(e.target.value)} />
                                    </div>
                                    <div className="flex-1 overflow-auto p-2 min-h-0">
                                        {filteredTools.length === 0 ? (
                                            <Empty
                                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                                description={connected ? '没有匹配的工具' : '连接后展示该令牌可见的 MCP 工具'}
                                                className="!my-10"
                                            />
                                        ) : (
                                            filteredTools.map(t => (
                                                <button key={t.name} type="button"
                                                    onClick={() => {
                                                        setSelectedTool(t);
                                                        setCallResult(null);
                                                        const defaults: Record<string, string> = {};
                                                        Object.entries(t.inputSchema?.properties ?? {}).forEach(([k, p]) => {
                                                            if (p.default != null) defaults[k] = String(p.default);
                                                        });
                                                        setFormValues(defaults);
                                                    }}
                                                    className={`w-full text-left px-3 py-2 mb-1 rounded-lg border transition-colors ${
                                                        selectedTool?.name === t.name
                                                            ? 'border-[rgba(91,141,239,0.40)] bg-[rgba(91,141,239,0.16)]'
                                                            : 'border-transparent hover:border-[#E7EEF9] hover:bg-white/70'
                                                    }`}>
                                                    <div className="flex items-center justify-between gap-2">
                                                        <span className="text-[13px] font-medium text-[#1F2A44] font-mono truncate">{t.name}</span>
                                                        <RightOutlined className="text-[10px] text-[#A9B4C9] shrink-0" />
                                                    </div>
                                                    <div className="text-[11px] leading-4 text-[#66738F] line-clamp-2 mt-0.5">{t.description}</div>
                                                </button>
                                            ))
                                        )}
                                    </div>
                                </section>

                                {/* 工具详情 */}
                                <section className="flex-1 min-w-0 overflow-auto scrollbar-stable">
                                    {!selectedTool ? (
                                        <div className="h-full flex flex-col items-center justify-center gap-3 px-6 text-center">
                                            <span
                                                className="w-14 h-14 rounded-2xl flex items-center justify-center text-[22px] text-[#2F62D8]"
                                                style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                                            >
                                                <ThunderboltOutlined />
                                            </span>
                                            <div className="text-[13px] text-[#66738F]">从左侧选择一个工具，查看参数并调用</div>
                                        </div>
                                    ) : (
                                        <div className="p-4 sm:p-5 max-w-4xl mx-auto space-y-4 pb-8">
                                            {/* 工具信息 */}
                                            <div className="surface-card p-4">
                                                <div className="flex items-start justify-between gap-3">
                                                    <div className="min-w-0">
                                                        <div className="font-mono font-semibold text-[#1F2A44] text-base break-all">{selectedTool.name}</div>
                                                        <div className="text-[13px] text-[#66738F] mt-1 leading-relaxed">{selectedTool.description}</div>
                                                    </div>
                                                    <Tag className="!m-0 shrink-0 !text-[10.5px]">{schemaProps.length} 个参数</Tag>
                                                </div>
                                            </div>

                                            {/* 参数 */}
                                            <div className="surface-card p-4">
                                                <div className="flex items-center justify-between mb-3">
                                                    <span className="text-sm font-semibold text-[#1F2A44]">调用参数</span>
                                                    <span className="text-[11px] text-[#8A94AB]">带 <span className="text-[#C2413A]">*</span> 为必填</span>
                                                </div>
                                                {schemaProps.length === 0 ? (
                                                    <div className="text-[13px] text-[#66738F] py-2">该工具无入参，直接运行即可</div>
                                                ) : (
                                                    <div className="space-y-3">
                                                        {schemaProps.map(([key, prop]) => (
                                                            <div key={key}>
                                                                <div className="text-xs mb-1 flex items-baseline gap-2 flex-wrap">
                                                                    <span className="font-mono text-[#3D4B66]">{key}</span>
                                                                    {requiredKeys.includes(key) && <span className="text-[#C2413A]">*</span>}
                                                                    {prop.description && <span className="text-[#8A94AB]">{prop.description}</span>}
                                                                </div>
                                                                {prop.enum ? (
                                                                    <Select
                                                                        className="w-full max-w-xl"
                                                                        value={formValues[key] ?? prop.default}
                                                                        onChange={v => setFormValues(prev => ({ ...prev, [key]: v }))}
                                                                        options={prop.enum.map(e => ({ value: e, label: e }))}
                                                                        allowClear
                                                                    />
                                                                ) : prop.type === 'boolean' ? (
                                                                    <Checkbox
                                                                        checked={formValues[key] === 'true'}
                                                                        onChange={e => setFormValues(prev => ({ ...prev, [key]: String(e.target.checked) }))}
                                                                    >{key}</Checkbox>
                                                                ) : prop.type === 'integer' || prop.type === 'number' ? (
                                                                    <InputNumber
                                                                        className="max-w-xl w-full"
                                                                        value={formValues[key] ? Number(formValues[key]) : undefined}
                                                                        onChange={v => setFormValues(prev => ({ ...prev, [key]: v == null ? '' : String(v) }))}
                                                                        placeholder={prop.description}
                                                                    />
                                                                ) : (
                                                                    <Input.TextArea
                                                                        className="max-w-xl"
                                                                        rows={2}
                                                                        value={formValues[key] ?? ''}
                                                                        onChange={e => setFormValues(prev => ({ ...prev, [key]: e.target.value }))}
                                                                        placeholder={prop.type === 'array' || prop.type === 'object' ? 'JSON 字符串' : prop.description}
                                                                    />
                                                                )}
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}
                                                <div className="flex flex-wrap gap-2 pt-4 mt-4 border-t border-[#F0F4FB]">
                                                    <Button type="primary" icon={<PlayCircleOutlined />} onClick={runTool} loading={calling}>
                                                        运行工具
                                                    </Button>
                                                    <Button icon={<CopyOutlined />} onClick={copyInput}>Copy Input</Button>
                                                </div>
                                            </div>

                                            {/* 结果 */}
                                            {callResult && (
                                                <div className="surface-card p-4">
                                                    <div className="flex items-center gap-2 mb-2">
                                                        <span className="text-sm font-semibold text-[#1F2A44]">调用结果</span>
                                                        <span
                                                            className="rounded-full px-2 py-[2px] text-[10.5px] leading-none"
                                                            style={callResult.ok
                                                                ? { background: 'rgba(91, 141, 239, 0.12)', color: '#2A57C6' }
                                                                : { background: 'rgba(180, 35, 24, 0.10)', color: '#B42318' }}
                                                        >
                                                            {callResult.ok ? '成功' : '失败'}
                                                        </span>
                                                    </div>
                                                    <ResultBody text={callResult.text} />
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </section>
                            </>
                        )}

                        {activeTab === 'resources' && (
                            <>
                                <section className="w-80 shrink-0 bg-white/30 border-r border-[#E4EBF7]/70 flex flex-col min-h-0">
                                    <div className="px-4 py-3 border-b border-[#F0F4FB] flex items-center justify-between gap-2">
                                        <div className="flex items-center gap-2 min-w-0">
                                            <span className="text-sm font-semibold text-[#1F2A44]">资源清单</span>
                                            <Tag className="!m-0 !text-[10.5px]">{resources.length}</Tag>
                                        </div>
                                        <Tooltip title="刷新资源列表（List Resources）">
                                            <Button size="small" type="text" icon={<ReloadOutlined />}
                                                disabled={!connected} onClick={listResources} />
                                        </Tooltip>
                                    </div>
                                    <div className="flex-1 overflow-auto p-2 min-h-0">
                                        {resources.length === 0 ? (
                                            <Empty
                                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                                description={connected ? '点击右上角刷新，列出该项目的 MCP 资源' : '连接后可浏览该项目的 MCP 资源'}
                                                className="!my-10"
                                            />
                                        ) : (
                                            resources.map(r => (
                                                <button key={r.uri} type="button"
                                                    onClick={() => readResource(r)}
                                                    className={`w-full text-left px-3 py-2 mb-1 rounded-lg border transition-colors ${
                                                        selectedResource?.uri === r.uri
                                                            ? 'border-[rgba(91,141,239,0.40)] bg-[rgba(91,141,239,0.16)]'
                                                            : 'border-transparent hover:border-[#E7EEF9] hover:bg-white/70'
                                                    }`}>
                                                    <div className="flex items-center justify-between gap-2">
                                                        <span className="text-[13px] font-medium text-[#1F2A44] truncate">{r.name ?? r.uri}</span>
                                                        {r.mimeType && <Tag className="!text-[10.5px] !m-0 shrink-0">{r.mimeType}</Tag>}
                                                    </div>
                                                    <div className="font-mono text-[11px] text-[#66738F] mt-0.5 truncate">{r.uri}</div>
                                                    {r.description && <div className="text-[11px] leading-4 text-[#8A94AB] mt-0.5 line-clamp-2">{r.description}</div>}
                                                </button>
                                            ))
                                        )}
                                    </div>
                                </section>
                                <section className="flex-1 min-w-0 overflow-auto scrollbar-stable">
                                    {!selectedResource ? (
                                        <div className="h-full flex flex-col items-center justify-center gap-3 px-6 text-center">
                                            <span
                                                className="w-14 h-14 rounded-2xl flex items-center justify-center text-[22px] text-[#2F62D8]"
                                                style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                                            >
                                                <FolderOpenOutlined />
                                            </span>
                                            <div className="text-[13px] text-[#66738F]">点击左侧资源，读取其内容</div>
                                        </div>
                                    ) : (
                                        <div className="p-4 sm:p-5 max-w-4xl mx-auto space-y-4 pb-8">
                                            <div className="surface-card p-4">
                                                <div className="text-base font-medium text-[#1F2A44] break-all">
                                                    {selectedResource.name ?? selectedResource.uri}
                                                </div>
                                                <div className="font-mono text-xs text-[#66738F] mt-1 break-all">{selectedResource.uri}</div>
                                                {selectedResource.mimeType && <Tag className="!mt-2 !text-[10.5px]">{selectedResource.mimeType}</Tag>}
                                            </div>
                                            <div className="surface-card p-4">
                                                <div className="flex items-center gap-2 mb-2">
                                                    <span className="text-sm font-semibold text-[#1F2A44]">读取结果</span>
                                                    {resourceText && (
                                                        <span
                                                            className="rounded-full px-2 py-[2px] text-[10.5px] leading-none"
                                                            style={resourceText.ok
                                                                ? { background: 'rgba(91, 141, 239, 0.12)', color: '#2A57C6' }
                                                                : { background: 'rgba(180, 35, 24, 0.10)', color: '#B42318' }}
                                                        >
                                                            {resourceText.ok ? '成功' : '失败'}
                                                        </span>
                                                    )}
                                                </div>
                                                {readingRes ? (
                                                    <div className="text-[13px] text-[#8A94AB] py-2">正在读取…</div>
                                                ) : resourceText ? (
                                                    <ResultBody text={resourceText.text} />
                                                ) : (
                                                    <div className="text-[13px] text-[#8A94AB] py-2">即将自动读取…</div>
                                                )}
                                            </div>
                                        </div>
                                    )}
                                </section>
                            </>
                        )}

                        {activeTab === 'ping' && (
                            <section className="flex-1 min-w-0 overflow-auto scrollbar-stable p-4 sm:p-6">
                                <div className="max-w-2xl mx-auto space-y-4">
                                    <div className="surface-card p-6 text-center">
                                        <div className="w-12 h-12 rounded-2xl bg-[rgba(91,141,239,0.10)] text-[#5B8DEF] flex items-center justify-center text-xl mx-auto">
                                            <WifiOutlined />
                                        </div>
                                        <div className="text-base font-medium text-[#1F2A44] mt-3">连通性检测</div>
                                        <div className="text-[13px] text-[#66738F] mt-1 leading-relaxed">
                                            向网关发送 JSON-RPC <span className="font-mono text-[#3D4B66]">ping</span>，验证令牌与端点是否可用
                                        </div>
                                        <div className="mt-5">
                                            <Button type="primary" icon={<LinkOutlined />} loading={pinging}
                                                className="px-5" onClick={doPing} disabled={!bearer}>
                                                发送 Ping
                                            </Button>
                                        </div>
                                        {pingResult && (
                                            <div className={`mt-5 inline-flex items-center gap-2 rounded-lg border px-4 py-2 text-sm ${
                                                pingResult.ok
                                                    ? 'border-[rgba(91,141,239,0.35)] text-[#2A57C6]'
                                                    : 'border-[#F2C6CE] text-[#B42318]'
                                            }`}>
                                                {pingResult.ok ? (
                                                    <><CheckCircleFilled /> 连通正常 · 延迟 <b className="tk-mono">{pingResult.ms}</b> ms</>
                                                ) : (
                                                    <><CloseCircleFilled /> 失败：{pingResult.msg}</>
                                                )}
                                            </div>
                                        )}
                                        <div className="mt-5 text-[11px] text-[#8A94AB]">
                                            限流：读 60 次/分 · 写 20 次/分（每令牌）；认证失败返回 JSON-RPC error -32001
                                        </div>
                                    </div>

                                    <div className="surface-card p-4 grid grid-cols-1 sm:grid-cols-2 gap-4">
                                        <div className="min-w-0">
                                            <div className="text-[11px] text-[#8A94AB] mb-1">端点</div>
                                            <div className="font-mono text-xs text-[#3D4B66] break-all">{effEndpoint}</div>
                                        </div>
                                        <div className="min-w-0">
                                            <div className="text-[11px] text-[#8A94AB] mb-1">令牌</div>
                                            <div className="text-xs text-[#3D4B66] truncate">
                                                {selectedToken
                                                    ? `${selectedToken.name}（${selectedToken.token_hint}）`
                                                    : <span className="text-[#8A94AB]">未选择</span>}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </section>
                        )}
                    </div>

                    {/* ══ 底部：编号调用历史 + 服务端通知（对齐 Inspector 底栏） */}
                    <div className="h-56 shrink-0 border-t border-[#E4EBF7]/70 bg-white/30 flex min-h-0">
                        <div className="flex-1 min-w-0 flex flex-col">
                            {/* 两栏表头统一固定高度：左侧含「清空」小按钮（24px）、右侧只有 Tag（22px），
                                用 padding 撑高会差 2px，导致两条分隔线对不齐 */}
                            <div className="h-10 shrink-0 px-4 border-b border-[#F0F4FB] flex items-center justify-between gap-2">
                                <div className="flex items-center gap-2">
                                    <span className="text-sm font-semibold text-[#1F2A44]">调用历史</span>
                                    <Tag className="!m-0 !text-[10.5px]">{history.length}</Tag>
                                </div>
                                <Button size="small" type="text" icon={<ClearOutlined />} disabled={history.length === 0}
                                    onClick={() => { setHistory([]); setExpandedHistory(null); }}>清空</Button>
                            </div>
                            <div className="flex-1 overflow-auto px-4 py-2 min-h-0">
                                {history.length === 0 ? (
                                    <div className="h-full flex flex-col items-center justify-center gap-1 text-xs text-[#A9B4C9]">
                                        <ApiOutlined />
                                        暂无调用记录
                                    </div>
                                ) : (
                                    history.map(h => (
                                        <div key={h.id} className="mb-1">
                                            <button type="button"
                                                onClick={() => setExpandedHistory(prev => (prev === h.id ? null : h.id))}
                                                className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-xs text-left border transition-colors ${
                                                    expandedHistory === h.id ? 'bg-[#F7F9FD] border-[#E7EEF9]' : 'border-transparent hover:bg-[#F7F9FD]'
                                                }`}>
                                                <CaretRightOutlined className={`text-[10px] text-[#66738F] transition-transform ${expandedHistory === h.id ? 'rotate-90' : ''}`} />
                                                <span className="text-[#8A94AB] font-mono w-5 shrink-0">{h.seq}.</span>
                                                <span className="font-mono text-[#3D4B66] truncate">{h.method}</span>
                                                <span className="ml-auto shrink-0">
                                                    {h.ok
                                                        ? <CheckCircleFilled className="text-[#2F62D8]" />
                                                        : <CloseCircleFilled className="text-[#C2413A]" />}
                                                </span>
                                                <span className="text-[#A9B4C9] shrink-0">{h.at}</span>
                                            </button>
                                            {expandedHistory === h.id && (
                                                <div className="mx-2 mb-2 p-2 bg-[#F7F9FD] border border-[#F0F4FB] rounded-md grid grid-cols-2 gap-3 font-mono text-[11px]">
                                                    <div className="min-w-0">
                                                        <div className="text-[#66738F] mb-1">请求</div>
                                                        <div className="overflow-auto max-h-32"><JsonNode value={h.request} depth={2} /></div>
                                                    </div>
                                                    <div className="min-w-0">
                                                        <div className="text-[#66738F] mb-1">响应</div>
                                                        <div className="overflow-auto max-h-32">
                                                            {h.response ? <JsonNode value={h.response} depth={2} /> : <span className="text-[#A9B4C9]">-</span>}
                                                        </div>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>
                        <div className="w-80 shrink-0 border-l border-[#F0F4FB] flex flex-col">
                            <div className="h-10 shrink-0 px-4 border-b border-[#F0F4FB] flex items-center justify-between gap-2">
                                <span className="text-sm font-semibold text-[#1F2A44]">服务端通知</span>
                                <Tag className="!m-0 !text-[10.5px]">0</Tag>
                            </div>
                            <div className="flex-1 px-4 py-2">
                                <div className="h-full flex flex-col items-center justify-center gap-1 text-xs text-[#A9B4C9] text-center">
                                    <ApiOutlined />
                                    <span>无服务端推送</span>
                                    <span className="text-[10px] text-[#A9B4C9]">（HTTP 无状态传输）</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </main>
            </div>

            {/* ── 签发令牌弹窗 */}
            <Modal
                title="签发 MCP 令牌" open={issueOpen} footer={null}
                onCancel={() => { setIssueOpen(false); setIssuedPlain(null); }}
            >
                {issuedPlain ? (
                    <div className="space-y-3 py-2">
                        <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-sm text-amber-700">
                            明文仅此一次，请立即复制保存（平台只存 SHA-256，无法找回）
                        </div>
                        <div className="flex gap-2">
                            <Input value={issuedPlain} readOnly className="font-mono !text-xs" />
                            <Button icon={<CopyOutlined />}
                                onClick={() => { navigator.clipboard.writeText(issuedPlain); message.success('已复制'); }} />
                        </div>
                        <Button type="primary" onClick={() => { setIssueOpen(false); setIssuedPlain(null); }}>
                            我已保存
                        </Button>
                    </div>
                ) : (
                    <div className="space-y-3 py-2">
                        {isAdmin && (
                            <div>
                                <div className="text-xs text-[#66738F] mb-1">持有人</div>
                                <Select className="w-full" placeholder="选择用户"
                                    value={issueForm.user_id ?? undefined}
                                    onChange={v => setIssueForm(f => ({ ...f, user_id: v }))}
                                    options={users.map(u => ({ value: u.id, label: `${u.username}${u.role === 'admin' ? '（管理员）' : ''}` }))} />
                            </div>
                        )}
                        {!isAdmin && (
                            <div className="text-xs text-[#66738F]">
                                持有人为当前账号（{currentUser?.username}）；令牌仅能访问你为成员的项目
                            </div>
                        )}
                        <div>
                            <div className="text-xs text-[#66738F] mb-1">绑定项目（令牌仅能访问该项目数据）</div>
                            <Select className="w-full" placeholder="选择项目"
                                value={issueForm.project_id ?? undefined}
                                onChange={v => setIssueForm(f => ({ ...f, project_id: v }))}
                                options={projects.map(p => ({ value: p.id, label: p.name }))} />
                        </div>
                        <div>
                            <div className="text-xs text-[#66738F] mb-1">令牌名称</div>
                            <Input value={issueForm.name} maxLength={64}
                                onChange={e => setIssueForm(f => ({ ...f, name: e.target.value }))}
                                placeholder="如：图谱检索-只读" />
                        </div>
                        <div className="flex items-center gap-4">
                            <Checkbox checked={issueForm.can_write}
                                onChange={e => setIssueForm(f => ({ ...f, can_write: e.target.checked }))}>
                                允许写入（add_entity / add_relationship）
                            </Checkbox>
                            <span className="flex items-center gap-1 text-sm text-[#66738F]">
                                有效期（天）
                                <InputNumber min={0} max={365} value={issueForm.expires_days}
                                    onChange={v => setIssueForm(f => ({ ...f, expires_days: v ?? 0 }))} />
                            </span>
                        </div>
                        <Button type="primary" block onClick={issue}>签发</Button>
                    </div>
                )}
            </Modal>
        </div>
    );
}

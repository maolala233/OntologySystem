// src/pages/McpConsolePage.tsx - MCP 服务控制台（R8，布局对齐 MCP Inspector v0.20.0）
// 左栏连接配置（端点 + sk-mcp- 令牌签发/选择/撤销 + 连接状态 + 服务端信息卡）
// │ 顶部 Tab（工具 / 资源 / Ping）│ 工具清单（搜索 + List Tools/Clear）
// │ 工具详情（Schema 动态表单 + Run Tool + 彩色 JSON 树结果）
// │ 底部编号调用历史（可展开请求/响应）+ 服务端通知占位。全部走平台 POST /mcp 网关。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    Button, Checkbox, Input, InputNumber, Modal, Select, Tag, Tooltip, message,
} from 'antd';
import {
    ApiOutlined, CaretRightOutlined, ClearOutlined, CopyOutlined, DisconnectOutlined,
    LinkOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, RightOutlined,
    SearchOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import {
    McpRpcError, createMcpToken, listMcpTokens, mcpRpc, revokeMcpToken,
    textFromCallResult,
    type McpTokenItem, type McpToolSpec,
} from '../api/mcp';
import { adminApi } from '../api/admin';
import { projectsApi } from '../api/projects';
import type { AdminUser } from '../api/admin';
import type { ProjectData } from '../types/ontology';

// ───────────────────────── JSON 彩色折叠树（对齐 Inspector 的 JSON Viewer）

function JsonLeaf({ value }: { value: unknown }) {
    if (typeof value === 'string') return <span className="text-emerald-600 break-all">"{value}"</span>;
    if (typeof value === 'number') return <span className="text-blue-600">{String(value)}</span>;
    if (typeof value === 'boolean') return <span className="text-orange-500">{String(value)}</span>;
    if (value === null || value === undefined) return <span className="text-gray-400 italic">null</span>;
    return <span className="text-gray-600">{String(value)}</span>;
}

function JsonNode({ name, value, depth = 0 }: { name?: string; value: unknown; depth?: number }) {
    const [open, setOpen] = useState(depth < 4);
    const isObj = value !== null && typeof value === 'object';
    if (!isObj) {
        return (
            <div className="leading-6">
                {name !== undefined && <span className="text-gray-700">{name}: </span>}
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
                className="inline-flex items-center gap-1 text-left hover:bg-gray-50 rounded px-0.5 -ml-0.5">
                <CaretRightOutlined className={`text-[10px] text-gray-400 transition-transform ${open ? 'rotate-90' : ''}`} />
                {name !== undefined && <span className="text-gray-800">{name}</span>}
                <span className="text-gray-400">{openB}</span>
                {!open && (
                    <span className="text-gray-400 text-xs">
                        {entries.length} 项 <span>{closeB}</span>
                    </span>
                )}
            </button>
            {open && (
                <>
                    <div className="ml-3 border-l border-gray-100 pl-3">
                        {entries.map(([k, v]) => <JsonNode key={k} name={k} value={v} depth={depth + 1} />)}
                    </div>
                    <div className="text-gray-400">{closeB}</div>
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
        <div className="relative border border-gray-200 rounded-lg bg-white">
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
            {copied && <span className="absolute bottom-1.5 right-2 text-[11px] text-gray-400">已复制</span>}
        </div>
    );
}

// ───────────────────────── 页面

type TabKey = 'tools' | 'resources' | 'ping' | 'docs';

/** 接入说明用的代码块：深色底 + 右上角复制 */
function CodeBlock({ title, code }: { title?: string; code: string }) {
    const [copied, setCopied] = useState(false);
    return (
        <div>
            <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-gray-500">{title}</span>
                <Button size="small" type="text" icon={<CopyOutlined />}
                    onClick={() => {
                        navigator.clipboard.writeText(code);
                        setCopied(true);
                        setTimeout(() => setCopied(false), 1500);
                    }} />
            </div>
            <div className="relative border border-gray-700 rounded-lg bg-gray-900 overflow-auto max-h-80">
                {copied && <span className="absolute bottom-1.5 right-2 text-[11px] text-green-400 z-10">已复制</span>}
                <pre className="p-3 font-mono text-[12px] leading-relaxed text-gray-100 whitespace-pre">{code}</pre>
            </div>
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

    const endpoint = `${window.location.origin.split(':').slice(0, 2).join(':')}:3001/mcp`;

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
            const items = await listMcpTokens();
            setTokens(items.filter(t => !t.revoked_at));
            setSelectedTokenId(prev => prev ?? items.find(t => !t.revoked_at)?.id ?? null);
        } catch {
            message.error('加载 MCP 令牌失败（需 admin）');
        }
    }, []);

    useEffect(() => {
        loadTokens();
        (async () => {
            try {
                setUsers(await adminApi.listUsers());
                setProjects(await projectsApi.getSelectableProjects());
            } catch { /* 下拉留空 */ }
        })();
    }, [loadTokens]);

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

    /** 调用 JSON-RPC 并自动写历史；返回 result 或抛 McpRpcError */
    const rpc = useCallback(async (method: string, params: Record<string, unknown> = {}) => {
        historySeq += 1;
        const request = { jsonrpc: '2.0', id: historySeq, method, params };
        try {
            const result = await mcpRpc(bearer, method, params);
            pushHistory(method, request, true, { jsonrpc: '2.0', id: historySeq, result });
            return result;
        } catch (e) {
            const err = e instanceof McpRpcError
                ? { code: e.code, message: e.message, data: e.data }
                : { code: -1, message: String(e) };
            pushHistory(method, request, false, { jsonrpc: '2.0', id: historySeq, error: err });
            throw e;
        }
    }, [bearer, pushHistory]);

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
        if (issueForm.user_id == null || issueForm.project_id == null || !issueForm.name.trim()) {
            message.warning('请填写持有人、绑定项目与令牌名称');
            return;
        }
        try {
            const created = await createMcpToken({
                user_id: issueForm.user_id, project_id: issueForm.project_id,
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
    }, [issueForm, loadTokens]);

    const doRevoke = useCallback(async (id: number) => {
        try {
            await revokeMcpToken(id);
            localStorage.removeItem(`mcp_plain_${id}`);
            if (selectedTokenId === id) { setSelectedTokenId(null); disconnect(); }
            loadTokens();
        } catch {
            message.error('撤销失败');
        }
    }, [selectedTokenId, loadTokens, disconnect]);

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

    const tabItems: { key: TabKey; label: string }[] = [
        { key: 'tools', label: '工具' },
        { key: 'resources', label: '资源' },
        { key: 'ping', label: 'Ping' },
        { key: 'docs', label: '接入说明' },
    ];

    // 接入说明示例中的令牌：已选令牌且有明文缓存则代入，否则占位
    const docTok = bearer || 'sk-mcp-<你的令牌>';

    return (
        <div className="flex h-full bg-gray-50 min-h-0">
            {/* ══ 左栏：连接配置（对齐 Inspector 侧栏） */}
            <aside className="w-72 shrink-0 bg-white border-r border-gray-200 p-4 flex flex-col gap-3 overflow-auto">
                <div className="flex items-center gap-2">
                    <ApiOutlined className="text-blue-500 text-lg" />
                    <span className="font-semibold text-gray-800">MCP 控制台</span>
                </div>

                <div>
                    <div className="text-xs text-gray-400 mb-1">传输方式</div>
                    <Select value="http" disabled className="w-full" options={[{ value: 'http', label: 'HTTP (Streamable)' }]} />
                </div>

                <div>
                    <div className="text-xs text-gray-400 mb-1">端点</div>
                    <div className="flex gap-1">
                        <Input size="small" value={endpoint} readOnly className="!text-[11px] font-mono" />
                        <Tooltip title="复制端点">
                            <Button size="small" icon={<CopyOutlined />}
                                onClick={() => { navigator.clipboard.writeText(endpoint); message.success('已复制'); }} />
                        </Tooltip>
                    </div>
                </div>

                <div>
                    <div className="text-xs text-gray-400 mb-1 flex items-center justify-between">
                        <span>sk-mcp- 令牌</span>
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
                        notFoundContent={<span className="text-xs text-gray-400">暂无令牌，点击「签发」创建</span>}
                    />
                    {selectedToken && (
                        <div className="text-[11px] text-gray-400 mt-1 flex items-center justify-between">
                            <span>
                                {selectedToken.can_write ? '读写' : '只读'}
                                {' · '}项目 #{selectedToken.project_id}
                                {selectedToken.expires_at && ` · 有效期至 ${selectedToken.expires_at.slice(0, 10)}`}
                            </span>
                            <Button size="small" type="link" danger className="!px-0 !text-[11px]"
                                onClick={() => doRevoke(selectedToken.id)}>撤销</Button>
                        </div>
                    )}
                </div>

                <div className="flex gap-2">
                    {!connected ? (
                        <Button type="primary" block icon={<PlayCircleOutlined />} onClick={connect} disabled={!bearer}>
                            连接
                        </Button>
                    ) : (
                        <>
                            <Button block icon={<ReloadOutlined />} onClick={connect}>重连</Button>
                            <Button block danger icon={<DisconnectOutlined />} onClick={disconnect}>断开</Button>
                        </>
                    )}
                </div>
                <div className="flex items-center gap-1.5 text-xs">
                    <span className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-gray-300'}`} />
                    <span className={connected ? 'text-green-600' : 'text-gray-500'}>{connected ? '已连接' : '未连接'}</span>
                </div>

                {/* 服务端信息卡（对齐 Inspector 连接后的 server 卡片） */}
                {connected && serverInfo && (
                    <div className="border border-gray-200 rounded-lg p-2.5 bg-gray-50">
                        <div className="flex items-center gap-1.5">
                            <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                            <span className="font-mono text-sm text-gray-800">{serverInfo.name ?? 'mcp-server'}</span>
                        </div>
                        <div className="text-[11px] text-gray-400 mt-1">
                            Version: {serverInfo.version ?? '-'}
                            {protocolVersion && ` · 协议 ${protocolVersion}`}
                        </div>
                    </div>
                )}

                <div className="mt-auto border-t border-gray-100 pt-2 text-[11px] text-gray-400 leading-relaxed">
                    明文令牌仅在签发时展示一次（本页内存留存）；对外服务请把
                    <span className="font-mono text-gray-500"> Authorization: Bearer sk-mcp-*</span>
                    配入任意支持 Streamable HTTP 的 MCP 客户端。
                </div>
            </aside>

            {/* ══ 右侧主区 */}
            <main className="flex-1 min-w-0 flex flex-col min-h-0">
                {/* 顶部 Tab（工具 / 资源 / Ping） */}
                <div className="px-4 pt-3 bg-white border-b border-gray-200">
                    <div className="inline-flex bg-gray-100 rounded-lg p-1 gap-1">
                        {tabItems.map(t => (
                            <button key={t.key} type="button"
                                onClick={() => setActiveTab(t.key)}
                                className={`px-4 py-1.5 rounded-md text-sm transition-all ${
                                    activeTab === t.key
                                        ? 'bg-white shadow-sm font-medium text-gray-800'
                                        : 'text-gray-500 hover:text-gray-700'
                                }`}>
                                {t.label}
                            </button>
                        ))}
                    </div>
                </div>

                {/* 内容区 */}
                <div className="flex-1 min-h-0 flex">
                    {activeTab === 'docs' && (
                        <div className="flex-1 overflow-auto p-4 bg-gray-50">
                            <div className="max-w-3xl mx-auto space-y-4 pb-8">
                                <div className="bg-white border border-gray-200 rounded-xl p-4">
                                    <div className="font-medium text-gray-800 mb-2">三步接入</div>
                                    <ol className="text-sm text-gray-600 list-decimal pl-5 space-y-1 leading-relaxed">
                                        <li>左侧「签发」创建 <span className="font-mono">sk-mcp-</span> 令牌：选持有人、绑定项目、勾是否允许写入——<b>明文只展示一次</b>，当场复制</li>
                                        <li>端点固定为 <span className="font-mono">{endpoint}</span>（HTTP Streamable，无状态 POST-only）</li>
                                        <li>把端点 + 令牌配到下方任意一种客户端 / 自己的 agent 里</li>
                                    </ol>
                                    <div className="mt-2 text-xs text-gray-400">
                                        令牌绑定单项目（数据隔离）；只读令牌不暴露写入工具；限流：读 60 次/分、写 20 次/分。
                                        {bearer ? ' 已检测到当前选中令牌，下方示例已代入。' : ' 尚未选令牌，示例中用占位符表示，替换为你的明文即可。'}
                                    </div>
                                </div>

                                <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
                                    <div className="font-medium text-gray-800">curl 调用</div>
                                    <CodeBlock title="① 握手 + 列出工具（tools/list）" code={`# initialize（唯一不需要令牌的方法）
curl -s -X POST ${endpoint} -H "Content-Type: application/json" \\
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}'

# 列出全部工具（需令牌）
curl -s -X POST ${endpoint} \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${docTok}" \\
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'`} />
                                    <CodeBlock title="② 调用 ask（GraphRAG 问答）" code={`curl -s -X POST ${endpoint} \\
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

                                <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
                                    <div className="font-medium text-gray-800">Python 调用（官方 MCP SDK）</div>
                                    <CodeBlock title="pip install mcp" code={`import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ENDPOINT = "${endpoint}"
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

                                <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
                                    <div className="font-medium text-gray-800">配到现成 MCP 客户端</div>
                                    <CodeBlock title="Cursor 等支持 HTTP Streamable 的客户端（mcpServers JSON）" code={`{
  "mcpServers": {
    "ontology-platform": {
      "url": "${endpoint}",
      "headers": { "Authorization": "Bearer ${docTok}" }
    }
  }
}`} />
                                    <CodeBlock title="Claude Code 命令行" code={`claude mcp add --transport http ontology-platform \\
  ${endpoint} \\
  --header "Authorization: Bearer ${docTok}"`} />
                                    <CodeBlock title="仅支持 stdio 的客户端（如桌面版 Claude）：mcp-remote 桥接" code={`{
  "mcpServers": {
    "ontology-platform": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "${endpoint}",
               "--header", "Authorization: Bearer ${docTok}"]
    }
  }
}`} />
                                </div>

                                <div className="bg-white border border-gray-200 rounded-xl p-4">
                                    <div className="font-medium text-gray-800 mb-2">注意事项</div>
                                    <ul className="text-sm text-gray-600 list-disc pl-5 space-y-1 leading-relaxed">
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
                            <section className="w-96 shrink-0 bg-white border-r border-gray-200 flex flex-col min-h-0">
                                <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                                    <span className="font-medium text-gray-700">工具（{tools.length}）</span>
                                    <div className="w-44">
                                        <Input size="small" allowClear prefix={<SearchOutlined className="text-gray-300" />}
                                            placeholder="搜索工具" value={toolSearch}
                                            onChange={e => setToolSearch(e.target.value)} />
                                    </div>
                                </div>
                                <div className="px-4 py-2 border-b border-gray-100 space-y-1.5">
                                    <Button block onClick={listTools} disabled={!connected}>List Tools</Button>
                                    <Button block disabled={tools.length === 0}
                                        onClick={() => { setTools([]); setSelectedTool(null); setCallResult(null); }}>
                                        Clear
                                    </Button>
                                </div>
                                <div className="flex-1 overflow-auto p-2 min-h-0">
                                    {filteredTools.length === 0 ? (
                                        <div className="text-xs text-gray-400 text-center py-10">
                                            {connected ? '没有匹配的工具' : '连接后展示该令牌可见的 MCP 工具'}
                                        </div>
                                    ) : (
                                        filteredTools.map(t => (
                                            <div key={t.name}
                                                onClick={() => {
                                                    setSelectedTool(t);
                                                    setCallResult(null);
                                                    const defaults: Record<string, string> = {};
                                                    Object.entries(t.inputSchema?.properties ?? {}).forEach(([k, p]) => {
                                                        if (p.default != null) defaults[k] = String(p.default);
                                                    });
                                                    setFormValues(defaults);
                                                }}
                                                className={`px-3 py-2 mb-1 rounded-lg cursor-pointer border transition-all ${
                                                    selectedTool?.name === t.name
                                                        ? 'border-blue-400 bg-blue-50'
                                                        : 'border-transparent hover:bg-gray-50'
                                                }`}>
                                                <div className="flex items-center justify-between">
                                                    <span className="text-sm font-medium text-gray-800 font-mono">{t.name}</span>
                                                    <RightOutlined className="text-[10px] text-gray-300" />
                                                </div>
                                                <div className="text-[11px] text-gray-500 line-clamp-2 mt-0.5">{t.description}</div>
                                            </div>
                                        ))
                                    )}
                                </div>
                            </section>

                            {/* 工具详情 */}
                            <section className="flex-1 min-w-0 overflow-auto p-4">
                                {!selectedTool ? (
                                    <div className="h-full flex items-center justify-center">
                                        <div className="border border-gray-200 rounded-xl bg-white px-8 py-6 text-gray-400 shadow-sm">
                                            <ThunderboltOutlined className="mr-2" />
                                            从左侧选择一个工具，查看详情并调用
                                        </div>
                                    </div>
                                ) : (
                                    <div className="max-w-3xl mx-auto">
                                        <div className="font-mono font-semibold text-gray-800 text-base">{selectedTool.name}</div>
                                        <div className="text-sm text-gray-500 mt-1 mb-4">{selectedTool.description}</div>

                                        <div className="space-y-3 mb-4">
                                            {schemaProps.length === 0 && (
                                                <div className="text-xs text-gray-400">该工具无入参，直接运行即可</div>
                                            )}
                                            {schemaProps.map(([key, prop]) => (
                                                <div key={key}>
                                                    <div className="text-xs mb-1">
                                                        <span className="font-mono text-gray-700">{key}</span>
                                                        {requiredKeys.includes(key) && <span className="text-red-400"> *</span>}
                                                        {prop.description && <span className="ml-2 text-gray-400">{prop.description}</span>}
                                                    </div>
                                                    {prop.enum ? (
                                                        <Select
                                                            className="w-full max-w-lg"
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
                                                            className="max-w-lg w-full"
                                                            value={formValues[key] ? Number(formValues[key]) : undefined}
                                                            onChange={v => setFormValues(prev => ({ ...prev, [key]: v == null ? '' : String(v) }))}
                                                            placeholder={prop.description}
                                                        />
                                                    ) : (
                                                        <Input.TextArea
                                                            className="max-w-lg"
                                                            rows={2}
                                                            value={formValues[key] ?? ''}
                                                            onChange={e => setFormValues(prev => ({ ...prev, [key]: e.target.value }))}
                                                            placeholder={prop.type === 'array' || prop.type === 'object' ? 'JSON 字符串' : prop.description}
                                                        />
                                                    )}
                                                </div>
                                            ))}
                                        </div>

                                        <div className="flex gap-2 mb-4">
                                            <Button type="primary" icon={<PlayCircleOutlined />} onClick={runTool} loading={calling}>
                                                运行工具
                                            </Button>
                                            <Button icon={<CopyOutlined />} onClick={copyInput}>Copy Input</Button>
                                        </div>

                                        {callResult && (
                                            <div>
                                                <div className="text-sm mb-2">
                                                    <span className="text-gray-600">工具结果：</span>
                                                    <span className={callResult.ok ? 'text-green-600 font-medium' : 'text-red-500 font-medium'}>
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
                            <section className="w-96 shrink-0 bg-white border-r border-gray-200 flex flex-col min-h-0">
                                <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                                    <span className="font-medium text-gray-700">资源（{resources.length}）</span>
                                </div>
                                <div className="px-4 py-2 border-b border-gray-100">
                                    <Button block onClick={listResources} disabled={!connected}>List Resources</Button>
                                </div>
                                <div className="flex-1 overflow-auto p-2 min-h-0">
                                    {resources.length === 0 ? (
                                        <div className="text-xs text-gray-400 text-center py-10">
                                            {connected ? '点击 List Resources 列出资源' : '连接后可浏览该项目的 MCP 资源'}
                                        </div>
                                    ) : (
                                        resources.map(r => (
                                            <div key={r.uri}
                                                onClick={() => readResource(r)}
                                                className={`px-3 py-2 mb-1 rounded-lg cursor-pointer border transition-all ${
                                                    selectedResource?.uri === r.uri
                                                        ? 'border-blue-400 bg-blue-50'
                                                        : 'border-transparent hover:bg-gray-50'
                                                }`}>
                                                <div className="flex items-center justify-between gap-2">
                                                    <span className="text-sm text-gray-800">{r.name ?? r.uri}</span>
                                                    {r.mimeType && <Tag className="!text-[10px] !m-0">{r.mimeType}</Tag>}
                                                </div>
                                                <div className="font-mono text-[11px] text-gray-400 mt-0.5">{r.uri}</div>
                                                {r.description && <div className="text-[11px] text-gray-500 mt-0.5">{r.description}</div>}
                                            </div>
                                        ))
                                    )}
                                </div>
                            </section>
                            <section className="flex-1 min-w-0 overflow-auto p-4">
                                {!selectedResource ? (
                                    <div className="h-full flex items-center justify-center">
                                        <div className="border border-gray-200 rounded-xl bg-white px-8 py-6 text-gray-400 shadow-sm">
                                            点击左侧资源读取内容
                                        </div>
                                    </div>
                                ) : (
                                    <div className="max-w-3xl mx-auto">
                                        <div className="text-base text-gray-800">{selectedResource.name ?? selectedResource.uri}</div>
                                        <div className="font-mono text-xs text-gray-400 mb-3">{selectedResource.uri}</div>
                                        {readingRes ? (
                                            <div className="text-sm text-gray-400">读取中…</div>
                                        ) : resourceText ? (
                                            <>
                                                <div className="text-sm mb-2">
                                                    <span className="text-gray-600">读取结果：</span>
                                                    <span className={resourceText.ok ? 'text-green-600 font-medium' : 'text-red-500 font-medium'}>
                                                        {resourceText.ok ? '成功' : '失败'}
                                                    </span>
                                                </div>
                                                <ResultBody text={resourceText.text} />
                                            </>
                                        ) : null}
                                    </div>
                                )}
                            </section>
                        </>
                    )}

                    {activeTab === 'ping' && (
                        <section className="flex-1 min-w-0 overflow-auto p-6">
                            <div className="max-w-xl mx-auto text-center py-10">
                                <div className="text-gray-500 mb-4">
                                    向网关发送 JSON-RPC <span className="font-mono text-gray-700">ping</span> 请求，验证令牌与端点连通性
                                </div>
                                <Button type="primary" size="large" icon={<LinkOutlined />} loading={pinging}
                                    onClick={doPing} disabled={!bearer}>
                                    Ping
                                </Button>
                                {pingResult && (
                                    <div className={`mt-5 text-sm ${pingResult.ok ? 'text-green-600' : 'text-red-500'}`}>
                                        {pingResult.ok
                                            ? <span className="font-medium">成功 · 延迟 {pingResult.ms} ms</span>
                                            : `失败：${pingResult.msg}`}
                                    </div>
                                )}
                                <div className="mt-6 text-xs text-gray-400">
                                    限流：读 60 次/分 · 写 20 次/分（每令牌）；认证失败返回 JSON-RPC error -32001
                                </div>
                            </div>
                        </section>
                    )}
                </div>

                {/* ══ 底部：编号调用历史 + 服务端通知（对齐 Inspector 底栏） */}
                <div className="h-56 shrink-0 border-t border-gray-200 bg-white flex min-h-0">
                    <div className="flex-1 min-w-0 flex flex-col">
                        <div className="px-4 py-2 border-b border-gray-100 flex items-center justify-between">
                            <span className="text-sm font-medium text-gray-700">调用历史</span>
                            <Button size="small" type="text" icon={<ClearOutlined />} disabled={history.length === 0}
                                onClick={() => { setHistory([]); setExpandedHistory(null); }}>清空</Button>
                        </div>
                        <div className="flex-1 overflow-auto px-4 py-2 min-h-0">
                            {history.length === 0 ? (
                                <div className="text-xs text-gray-300 italic pt-2">暂无调用记录</div>
                            ) : (
                                history.map(h => (
                                    <div key={h.id} className="mb-1">
                                        <button type="button"
                                            onClick={() => setExpandedHistory(prev => (prev === h.id ? null : h.id))}
                                            className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-xs text-left border transition-colors ${
                                                expandedHistory === h.id ? 'bg-gray-50 border-gray-200' : 'border-transparent hover:bg-gray-50'
                                            }`}>
                                            <CaretRightOutlined className={`text-[10px] text-gray-400 transition-transform ${expandedHistory === h.id ? 'rotate-90' : ''}`} />
                                            <span className="text-gray-400">{h.seq}.</span>
                                            <span className="font-mono text-gray-700">{h.method}</span>
                                            <span className={`ml-auto ${h.ok ? 'text-green-500' : 'text-red-400'}`}>{h.ok ? '✓' : '✗'}</span>
                                            <span className="text-gray-300">{h.at}</span>
                                        </button>
                                        {expandedHistory === h.id && (
                                            <div className="mx-2 mb-2 p-2 bg-gray-50 border border-gray-100 rounded-md grid grid-cols-2 gap-3 font-mono text-[11px]">
                                                <div className="min-w-0">
                                                    <div className="text-gray-400 mb-1">请求</div>
                                                    <div className="overflow-auto max-h-32"><JsonNode value={h.request} depth={2} /></div>
                                                </div>
                                                <div className="min-w-0">
                                                    <div className="text-gray-400 mb-1">响应</div>
                                                    <div className="overflow-auto max-h-32">
                                                        {h.response ? <JsonNode value={h.response} depth={2} /> : <span className="text-gray-300">-</span>}
                                                    </div>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                    <div className="w-96 shrink-0 border-l border-gray-100 flex flex-col">
                        <div className="px-4 py-2 border-b border-gray-100 flex items-center justify-between">
                            <span className="text-sm font-medium text-gray-700">服务端通知</span>
                        </div>
                        <div className="flex-1 px-4 py-2">
                            <div className="text-xs text-gray-300 italic pt-2">
                                暂无通知（无状态 HTTP 传输，无服务端推送）
                            </div>
                        </div>
                    </div>
                </div>
            </main>

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
                        <div>
                            <div className="text-xs text-gray-500 mb-1">持有人</div>
                            <Select className="w-full" placeholder="选择用户"
                                value={issueForm.user_id ?? undefined}
                                onChange={v => setIssueForm(f => ({ ...f, user_id: v }))}
                                options={users.map(u => ({ value: u.id, label: `${u.username}${u.role === 'admin' ? '（管理员）' : ''}` }))} />
                        </div>
                        <div>
                            <div className="text-xs text-gray-500 mb-1">绑定项目（令牌仅能访问该项目数据）</div>
                            <Select className="w-full" placeholder="选择项目"
                                value={issueForm.project_id ?? undefined}
                                onChange={v => setIssueForm(f => ({ ...f, project_id: v }))}
                                options={projects.map(p => ({ value: p.id, label: p.name }))} />
                        </div>
                        <div>
                            <div className="text-xs text-gray-500 mb-1">令牌名称</div>
                            <Input value={issueForm.name} maxLength={64}
                                onChange={e => setIssueForm(f => ({ ...f, name: e.target.value }))}
                                placeholder="如：图谱检索-只读" />
                        </div>
                        <div className="flex items-center gap-4">
                            <Checkbox checked={issueForm.can_write}
                                onChange={e => setIssueForm(f => ({ ...f, can_write: e.target.checked }))}>
                                允许写入（add_entity / add_relationship）
                            </Checkbox>
                            <span className="flex items-center gap-1 text-sm text-gray-500">
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

// src/pages/McpConsolePage.tsx - MCP 服务控制台（R8）
// 页面骨架：左栏连接配置卡（页头信息/指标收在卡内）+ 右栏 Tabs 卡 + 底部调用历史卡
// （工具 / 资源 / Ping / 接入说明）+ 底部全宽可折叠调用历史卡。
// 连接握手、工具/资源调用全部走平台 POST /mcp 网关。
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Input, InputNumber, Modal, Select, Tabs, Tooltip } from 'antd';
import { message, notifyError } from '../shared/feedback';
import EmptyState from '../components/EmptyState';
import { Dot, Note, SectionHead, TkTag, TONE_INK } from '../shared/consoleUi';
import { useImeSafeSearch } from '../shared/useImeSafeSearch';
import {
    ApiOutlined, BookOutlined, CaretRightOutlined, CheckCircleFilled, ClearOutlined, CloseCircleFilled,
    CopyOutlined, DeleteOutlined, DisconnectOutlined, FolderOpenOutlined, InfoCircleOutlined,
    LinkOutlined, LoadingOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, RightOutlined,
    DownOutlined, SearchOutlined, ThunderboltOutlined, ToolOutlined, UndoOutlined, UpOutlined, WarningOutlined, WifiOutlined,
} from '@ant-design/icons';
import {
    McpRpcError, createMcpToken, createMyMcpToken, getDefaultMcpEndpoint,
    getMcpEndpoint, listMcpTokens, listMyMcpTokens, getPlainToken, savePlainToken, dropPlainToken,
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

const MCP_SKILL_TEXT = `---
name: ontology-platform-retrieval
description: 查询 Ontology 平台项目图谱中的实体、关系、接口清单、路径与原文证据。
---

# 本体图谱检索 Skill

优先使用完整包里的脚本（只读、分页、计数闭合）：
- python scripts/check.py
- python scripts/retrieve.py count --class 前端接口
- python scripts/retrieve.py group --class 前端接口 --by 登记人

先看结构，再查数据：
1. graph_schema_get / entity_type_list 确认类型；
2. relation_type_list 确认关系谓词；
3. entity_find 做精确过滤（filter 支持 =、in、and、or）；
4. semantic_search 做语义召回，再用 entity_get 落到具体实体。

关系与路径：neighbors_get、relations_between、subgraph_expand、shortest_path、paths_find、path_exists。

计数：优先 query_facts(kind=entities)，用 total 而不是当前页条数；按属性分组时各组之和必须等于 total，缺失属性归入“未标注”。

回答：先给结论和总数，再给分组/清单，最后给 dataId、label、原文切片或文档名；图谱没有直接关系就明确说“未建立该关系”。

只读令牌不能写入；需要新增实体/关系时使用 can_write 令牌调用 add_entity / add_relationship。`;


function JsonLeaf({ value }: { value: unknown }) {
    if (typeof value === 'string') return <span className="break-all text-[#1B87AD]">"{value}"</span>;
    if (typeof value === 'number') return <span className="text-[#2F62D8]">{String(value)}</span>;
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
        <div className="relative border border-[#E7EEF9] rounded-lg bg-[#FBFDFF]">
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
                        icon={copied ? <CheckCircleFilled className="text-[#5AC8FA]" /> : <CopyOutlined />}
                        onClick={() => {
                            navigator.clipboard.writeText(text);
                            setCopied(true);
                            setTimeout(() => setCopied(false), 1500);
                        }} />
                </Tooltip>
            </div>
            <pre className="m-0 p-3 font-mono text-[12px] leading-relaxed text-[#C9D4FF] overflow-auto max-h-80 whitespace-pre">{text}</pre>
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
        () => (selectedTokenId != null ? getPlainToken(selectedTokenId) : ''),
        [selectedTokenId],
    );
    const [connected, setConnected] = useState(false);
    // 连接/重连进行中：握手是两次串行往返（initialize → tools/list），
    // 不给按钮 loading 的话连点会叠出两轮握手（2026-11-18）
    const [connecting, setConnecting] = useState(false);
    const [reissuing, setReissuing] = useState(false);

    // ── Tab 与数据
    const [activeTab, setActiveTab] = useState<TabKey>('tools');
    const [tools, setTools] = useState<McpToolSpec[]>([]);
    // 工具搜索（2026-11-25）：输入框即时回显，组字结束/停顿 200ms 后才过滤列表
    const {
        value: toolSearch,
        setValue: setToolSearch,
        query: toolSearchQuery,
        inputProps: toolSearchInputProps,
    } = useImeSafeSearch(200);
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
    // 底部调用历史默认收起成标题条（全宽卡片，展开看请求/响应）
    const [historyOpen, setHistoryOpen] = useState(false);
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
            const alive = items.filter(t => !t.revoked_at);
            setTokens(alive);
            // 不再默认选中第一个令牌（2026-11-19 用户反馈）：列表是异步回来的，
            // 自动选中会让令牌信息块在进页面时"从无到有"出现，把下面的「连接」按钮顶下去；
            // 而且第一个令牌多半没有明文（明文只在签发时展示一次），选中了也连不上。
            // 只自动恢复当前标签页里还留着明文的令牌——只有这种能直接连
            setSelectedTokenId(prev => prev ?? alive.find(t => !!getPlainToken(t.id))?.id ?? null);
        } catch (e) {
            notifyError(e, isAdmin ? '加载 MCP 令牌失败（需 admin）' : '加载 MCP 令牌失败');
        }
    }, [isAdmin]);

    // 进页面只拉令牌列表（页面主体）。签发弹窗里的「持有人 / 项目」下拉按需加载：
    // 以前一进页面就白拉一次 /api/projects/my（实测 750ms+，admin 还要再拉用户与公共项目），
    // 而多数人根本不签发令牌（2026-11-18）
    const pickerLoadedRef = useRef(false);
    const [pickerLoading, setPickerLoading] = useState(false);
    useEffect(() => {
        if (!issueOpen || pickerLoadedRef.current) return;
        pickerLoadedRef.current = true;
        setPickerLoading(true);
        (async () => {
            try {
                const [u, p] = await Promise.all([
                    isAdmin ? adminApi.listUsers() : Promise.resolve<AdminUser[]>([]),
                    isAdmin ? projectsApi.getSelectableProjects() : projectsApi.getMyProjects(),
                ]);
                if (isAdmin) setUsers(u); // 仅 admin 需要持有人下拉
                setProjects(p);
            } catch {
                pickerLoadedRef.current = false; // 失败允许下次打开弹窗重试
            } finally {
                setPickerLoading(false);
            }
        })();
    }, [issueOpen, isAdmin]);

    useEffect(() => {
        loadTokens();
    }, [loadTokens]);

    const disconnect = useCallback(() => {
        setConnected(false);
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
    const rpc = useCallback(async (method: string, params: Record<string, unknown> = {},
                                 tokenOverride?: string) => {
        historySeq += 1;
        const request = { jsonrpc: '2.0', id: historySeq, method, params };
        const authToken = tokenOverride ?? bearer;
        try {
            const result = await mcpRpc(authToken, method, params, endpoint.trim() || undefined);
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

    // 连接 = initialize → tools/list；tokenOverride 用于“续签后立即连接”，
    // 避免 setState 尚未生效时读到旧 bearer。
    const connect = useCallback(async (tokenOverride?: string) => {
        const authToken = tokenOverride ?? bearer;
        if (!authToken) {
            message.warning('请先选择或签发令牌（明文仅签发时可见，已存本页内存）');
            return;
        }
        if (connecting) return;
        setConnecting(true);
        disconnect();
        try {
            await rpc('initialize', { protocolVersion: '2024-11-05', capabilities: {} }, authToken);
            const result = await rpc('tools/list', {}, authToken);
            const list = (result?.tools as McpToolSpec[]) ?? [];
            setTools(list);
            setConnected(true);
            message.success(`已连接：${list.length} 个工具`);
        } catch (e) {
            setConnected(false);
            const msg = e instanceof McpRpcError ? `RPC ${e.code}: ${e.message}` : String(e);
            message.error(`连接失败：${msg}`);
        } finally {
            setConnecting(false);
        }
    }, [bearer, rpc, disconnect, connecting]);

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
        // 不清空上一轮结果：结果卡一旦卸载，右侧滚动容器会瞬间变矮、滚动位置被夹到顶部，
        // 等结果回来又弹下去。这里保留旧结果（置灰 + 角标显示「运行中」），新结果到了再原地替换。
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
            savePlainToken(created.id, created.token); // 仅当前标签页（sessionStorage），不落 localStorage
            setIssuedPlain(created.token);
            await loadTokens();
            setSelectedTokenId(created.id);
        } catch (e: any) {
            notifyError(e, '签发失败');
        }
    }, [isAdmin, currentUser, issueForm, loadTokens]);

    const doRevoke = useCallback(async (id: number) => {
        try {
            if (isAdmin) await revokeMcpToken(id); else await revokeMyMcpToken(id);
            dropPlainToken(id);
            if (selectedTokenId === id) { setSelectedTokenId(null); disconnect(); }
            loadTokens();
        } catch (e) {
            notifyError(e, '撤销失败');
        }
    }, [isAdmin, selectedTokenId, loadTokens, disconnect]);

    const schemaProps = Object.entries(selectedTool?.inputSchema?.properties ?? {});
    const requiredKeys = selectedTool?.inputSchema?.required ?? [];
    const filteredTools = toolSearchQuery
        ? tools.filter(t => `${t.name} ${t.description ?? ''}`.toLowerCase().includes(toolSearchQuery.toLowerCase()))
        : tools;
    const selectedToken = tokens.find(t => t.id === selectedTokenId);

    const tokenState = (t: McpTokenItem): string => {
        if (t.revoked_at) return '已撤销';
        if (t.expires_at && new Date(t.expires_at).getTime() < Date.now()) return '已过期';
        return '有效';
    };

    // 有效令牌但本页没有明文时：预填同项目/同名/同权限，打开签发弹窗，
    // 让用户一次点击拿到可用的新明文，而不是只看到“请重新签发”的死提示。
    const openReissue = useCallback(() => {
        if (!selectedToken) return;
        setIssueForm({
            user_id: selectedToken.user_id,
            project_id: selectedToken.project_id,
            name: selectedToken.name,
            can_write: selectedToken.can_write,
            expires_days: 30,
        });
        setIssuedPlain(null);
        setIssueOpen(true);
    }, [selectedToken]);

    // 一键续签并连接：旧令牌的明文不可恢复，但用户不该被迫理解这套机制。
    // 直接生成同项目/同权限的新令牌，存本页明文，并立刻完成握手。
    const reissueAndConnect = useCallback(async () => {
        if (!selectedToken || reissuing) return;
        setReissuing(true);
        try {
            const created = isAdmin
                ? await createMcpToken({
                    user_id: selectedToken.user_id,
                    project_id: selectedToken.project_id,
                    name: selectedToken.name,
                    can_write: selectedToken.can_write,
                    expires_days: 30,
                })
                : await createMyMcpToken({
                    project_id: selectedToken.project_id,
                    name: selectedToken.name,
                    can_write: selectedToken.can_write,
                    expires_days: 30,
                });
            savePlainToken(created.id, created.token);
            await loadTokens();
            setSelectedTokenId(created.id);
            await connect(created.token);
            message.success('已续签并连接；新令牌明文只保存在本标签页');
        } catch (e: any) {
            message.error(e?.response?.data?.error?.message || '续签失败，请检查项目权限');
        } finally {
            setReissuing(false);
        }
    }, [selectedToken, reissuing, isAdmin, loadTokens, connect]);

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
        <div className="h-full flex flex-col app-canvas">
            <style>{`
                /* Tab 条做成与面板同材质的横条：不再裸在画布上，也不做成「卡片套卡片」。
                   内部改用分段胶囊——在横条里画下划线会和描边打架。 */
                /* 卡片材质对齐全站「服务卡 / 角色卡」：冷白 → 淡品牌蓝的斜向渐变，
                   末段让画布透一点上来；纯白平铺在浅蓝画布上会显得没有厚度。 */
                .mcp-page .surface-card {
                    border-color: #E9EEF8;
                    border-radius: 16px;
                    background: linear-gradient(160deg, #FFFFFF 0%, rgba(246, 250, 255, 1) 40%, rgba(91, 141, 239, 0.15) 100%);
                    box-shadow:
                        inset 0 1px 0 rgba(255, 255, 255, 0.95),
                        0 14px 34px -26px rgba(15, 23, 42, 0.24),
                        0 2px 5px rgba(15, 23, 42, 0.035);
                }
                /* Tab 条沿用站内分段控件（我的项目「我的 / 全部」）那套配方：
                   淡品牌蓝轨道 + 白色滑块，选中态靠「面抬起来」而不是描边——
                   原来那条内描边蓝框压在渐变卡上太硬，滑块和轨道也几乎同色，看着别扭。 */
                .mcp-page .mcp-page-tabs > .ant-tabs-nav {
                    margin-bottom: 12px;
                    width: fit-content;
                    max-width: 100%;
                    padding: 4px;
                    border-radius: 14px;
                    border: 1px solid rgba(91, 141, 239, 0.16);
                    background: rgba(91, 141, 239, 0.075);
                    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.55), 0 2px 6px rgba(15, 23, 42, 0.03);
                }
                .mcp-page .mcp-page-tabs > .ant-tabs-nav::before { border-bottom: 0; }
                .mcp-page .mcp-page-tabs .ant-tabs-nav-list { gap: 2px; }
                .mcp-page .mcp-page-tabs .ant-tabs-tab {
                    margin: 0 !important;
                    padding: 4px 12px !important;
                    border-radius: 10px;
                    transition: background-color .18s ease, color .18s ease;
                }
                .mcp-page .mcp-page-tabs .ant-tabs-tab:not(.ant-tabs-tab-active):hover {
                    background: rgba(255, 255, 255, 0.62);
                }
                .mcp-page .mcp-page-tabs .ant-tabs-tab-active {
                    background: #FFFFFF;
                    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.14);
                }
                .mcp-page .mcp-page-tabs .ant-tabs-ink-bar { display: none; }
                .mcp-page .mcp-page-tabs .ant-tabs-tab .ant-tabs-tab-btn {
                    color: #52607A;
                    font-size: 12.5px;
                    transition: color .18s ease;
                }
                .mcp-page .mcp-page-tabs .ant-tabs-tab-active .ant-tabs-tab-btn { color: #2A57C6; font-weight: 600; }
            `}</style>
            <Navbar breadcrumbs={[{ title: '首页', path: '/' }, { title: '工具' }, { title: 'MCP 服务' }]} />
            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable mcp-page">
                <div className="grid gap-4 xl:h-[clamp(520px,calc(100vh_-_164px),680px)] xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
                    {/* ── 左栏：连接配置。Tabs 有定高的页签里让这张卡撑满同一行，
                        底部说明用 mt-auto 贴在卡底，两栏下沿就能对齐（接入说明是长文，不拉伸） ── */}
                    <div className="min-w-0 space-y-4 xl:flex xl:flex-col">
                        <section className="surface-card p-5 tk-controls xl:flex xl:flex-1 xl:flex-col">
                            {/* 页头信息收进卡内：右侧 Tabs 比这张卡高，标题/说明/指标放这里补齐高度，
                                也不再单占一条顶栏——信息和它描述的操作就在同一张卡里 */}
                            <div className="mb-4 flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                                        <h1 className="text-[17px] font-bold leading-tight tracking-[-0.01em] text-[#0F172A]">MCP 服务</h1>
                                        <span className="tk-mono text-[10px] tracking-[0.18em] text-[#8A94AB]">MCP CONSOLE</span>
                                    </div>
                                    <p className="mt-1 text-[11.5px] leading-relaxed text-[#66738F]">
                                        连接配置、令牌与工具调用 · 无状态 POST 网关
                                    </p>
                                </div>
                                <div className="flex shrink-0 flex-col items-end gap-2">
                                    <TkTag tone={connected ? 'brand' : 'quiet'}>
                                        <span className="h-1.5 w-1.5 rounded-full" style={{ background: connected ? '#2F62D8' : '#B7C1D4' }} />
                                        {connected ? '已连接' : '未连接'}
                                    </TkTag>
                                    <Button size="small" className="tk-soft-btn" icon={<PlusOutlined />}
                                        onClick={() => { setIssuedPlain(null); setIssueOpen(true); }}>签发令牌</Button>
                                </div>
                            </div>

                            <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1.5">
                                {[
                                    { label: '有效令牌', value: tokens.filter(t => tokenState(t) === '有效').length, tone: 'brand' as const },
                                    { label: '工具', value: connected ? tools.length : 0, tone: 'violet' as const },
                                    { label: '资源', value: connected ? resources.length : 0, tone: 'cyan' as const },
                                    { label: '调用', value: history.length, tone: 'amber' as const },
                                ].map((st, i) => (
                                    <Fragment key={st.label}>
                                        {i > 0 && <span className="h-3 w-px shrink-0 bg-[#DCE4F2]" aria-hidden />}
                                        <span className="flex items-center gap-1.5">
                                            <Dot tone={st.tone} />
                                            <span className="text-[11.5px] text-[#8A94AB]">{st.label}</span>
                                            <span className="tk-mono text-[13px] font-semibold leading-none" style={{ color: TONE_INK[st.tone] }}>{st.value}</span>
                                        </span>
                                    </Fragment>
                                ))}
                            </div>

                            <div className="mb-4 h-px bg-[#EDF1F8]" />

                            <SectionHead
                                className="mb-3"
                                title="连接配置"
                                extra={<TkTag mono>HTTP Streamable</TkTag>}
                            />

                            <div className="space-y-3">
                                <div>
                                    <div className="mb-1.5 flex items-center justify-between gap-2">
                                        <span className="text-[12px] text-[#66738F]">端点</span>
                                        {endpointDirty && (
                                            <Tooltip title="恢复默认端点">
                                                <Button size="small" type="text" icon={<UndoOutlined />} className="!px-1"
                                                    onClick={() => changeEndpoint(defaultEndpoint)} />
                                            </Tooltip>
                                        )}
                                    </div>
                                    <div className="flex gap-1.5">
                                        <Tooltip title="默认按页面地址自动推导；容器化/反向代理部署可直接改为实际可达地址（如 https://域名/mcp）">
                                            <Input size="small" value={endpoint}
                                                onChange={e => changeEndpoint(e.target.value)}
                                                placeholder={defaultEndpoint}
                                                className="!text-[11px] font-mono" />
                                        </Tooltip>
                                        <Tooltip title="复制端点">
                                            <Button size="small" type="text" icon={<CopyOutlined />} className="!px-1"
                                                onClick={() => { navigator.clipboard.writeText(endpoint.trim() || defaultEndpoint); message.success('已复制端点'); }} />
                                        </Tooltip>
                                    </div>
                                    <div className="mt-1 text-[11px] text-[#8A94AB]">无状态 POST · 每次请求独立</div>
                                </div>

                                <div>
                                    <div className="mb-1.5 text-[12px] text-[#66738F]">sk-mcp- 令牌</div>
                                    <Select
                                        value={selectedTokenId ?? undefined}
                                        onChange={v => { setSelectedTokenId(v); disconnect(); }}
                                        placeholder="选择令牌"
                                        className="w-full"
                                        optionLabelProp="label"
                                        options={tokens.map(t => ({
                                            value: t.id,
                                            disabled: tokenState(t) !== '有效',
                                            label: `${t.name}（${t.token_hint}）${tokenState(t) === '有效' ? '' : ` · ${tokenState(t)}`}`,
                                        }))}
                                        notFoundContent={<span className="text-xs text-[#66738F]">暂无令牌，点右上「签发令牌」创建</span>}
                                    />
                                    <div className="mt-2 h-9">
                                        {selectedToken ? (
                                            <div className="flex h-full w-full items-center justify-between gap-2 rounded-lg border border-[rgba(91,141,239,0.16)] bg-[rgba(91,141,239,0.06)] px-2.5">
                                                <span className="flex min-w-0 items-center gap-1.5">
                                                    <TkTag tone={selectedToken.can_write ? 'amber' : 'brand'} className="shrink-0">
                                                        {selectedToken.can_write ? '读写' : '只读'}
                                                    </TkTag>
                                                    <span className="text-[11px] text-[#66738F] truncate">项目 #{selectedToken.project_id}</span>
                                                    {selectedToken.expires_at && (
                                                        <span className="shrink-0 text-[11px] text-[#8A94AB]">
                                                            至 {selectedToken.expires_at.slice(0, 10)}
                                                        </span>
                                                    )}
                                                </span>
                                                <span className="flex items-center gap-0.5">
                                                    {bearer && (
                                                        <Tooltip title="复制令牌明文（仅本标签页有）">
                                                            <Button size="small" type="text" icon={<CopyOutlined />}
                                                                className="!px-1"
                                                                onClick={() => { navigator.clipboard.writeText(bearer); message.success('已复制令牌'); }} />
                                                        </Tooltip>
                                                    )}
                                                    <Tooltip title="撤销该令牌">
                                                        <Button size="small" type="text" danger icon={<DeleteOutlined />}
                                                            className="!px-1" onClick={() => doRevoke(selectedToken.id)} />
                                                    </Tooltip>
                                                </span>
                                            </div>
                                        ) : (
                                            <span className="flex h-full items-center text-[11px] text-[#A9B4C9]">
                                                先选择或签发令牌后才能连接
                                            </span>
                                        )}
                                    </div>
                                </div>

                                <div className="pt-1">
                                    <div className="flex gap-2">
                                        {!connected ? (
                                            <Button type="primary" block className="tk-primary-btn" icon={<PlayCircleOutlined />} onClick={() => connect()} loading={connecting} disabled={!bearer || connecting}>
                                                连接
                                            </Button>
                                        ) : (
                                            <>
                                                <Button block className="tk-soft-btn" icon={<ReloadOutlined />} onClick={() => connect()} loading={connecting} disabled={connecting}>重连</Button>
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
                                    {!bearer && selectedTokenId != null && (
                                        <Note tone="amber" className="mt-2" icon={<WarningOutlined />}>
                                            <div className="text-[11.5px] leading-relaxed">
                                                <div className="tk-note-title">这枚令牌以前签发过，本页没有它的密钥</div>
                                                <div className="mt-0.5">
                                                    平台只保存哈希，无法恢复明文。点下面按钮会自动生成一枚同项目、同权限的新令牌并直接连接。
                                                </div>
                                                <div className="mt-2 flex items-center gap-2">
                                                    <Button size="small" type="primary" className="tk-primary-btn" loading={reissuing}
                                                            onClick={reissueAndConnect}>
                                                        一键续签并连接
                                                    </Button>
                                                    <Button size="small" type="link" className="!px-0 !text-[11px]"
                                                            onClick={openReissue}>
                                                        手动调整后签发
                                                    </Button>
                                                </div>
                                            </div>
                                        </Note>
                                    )}
                                </div>
                            </div>
                            {/* 明文说明收进连接配置卡尾部：只是这张卡的补充信息，不再单独占一张卡 */}
                            <div className="mt-4 flex items-start gap-2 border-t border-[#EDF1F8] pt-3 text-[11px] leading-relaxed text-[#8A94AB] xl:mt-auto">
                                <InfoCircleOutlined className="mt-[2px] shrink-0 text-[#A9B4C9]" />
                                <span>
                                    明文令牌仅签发时展示一次（本页内存留存）；对外服务请把
                                    <span className="font-mono text-[#3D4B66]"> Authorization: Bearer sk-mcp-* </span>
                                    配入任意支持 Streamable HTTP 的 MCP 客户端。
                                </span>
                            </div>
                        </section>
                    </div>

                    {/* ── 右栏：Tabs。导航条固定，内容块吃满剩余高度，各页签自己滚 ── */}
                    <div className="min-w-0 xl:flex xl:min-h-0 xl:flex-col">
                        <Tabs
                            activeKey={activeTab}
                            onChange={key => setActiveTab(key as TabKey)}
                            className="mcp-page-tabs xl:shrink-0"
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
                        />

                        {activeTab === 'docs' && (
                            <div className="space-y-4 pb-0 xl:min-h-0 xl:flex-1 xl:overflow-y-auto xl:pr-1">
                                    {/* 当前配置速览 */}
                                    <div className="surface-card p-4">
                                        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                                            <SectionHead title="当前配置" hint={<TkTag mono>HTTP Streamable</TkTag>} />
                                            <span className="font-mono text-xs text-[#52607A] break-all">{effEndpoint}</span>
                                            <span className="h-3.5 w-px shrink-0 bg-[#DCE4F2]" aria-hidden />
                                            <span className="text-xs text-[#66738F]">
                                                {selectedToken
                                                    ? `当前令牌：${selectedToken.name}（${selectedToken.token_hint}）`
                                                    : '尚未选择令牌，示例中用占位符表示'}
                                            </span>
                                        </div>
                                    </div>

                                    <div className="surface-card p-5">
                                        <SectionHead className="mb-3" title="三步接入" hint="签发令牌 → 配置端点 → 接入客户端" />
                                        <ol className="text-sm text-[#52607A] list-decimal pl-5 space-y-1.5 leading-relaxed">
                                            <li>左侧「签发令牌」创建 <span className="font-mono">sk-mcp-</span> 令牌：{isAdmin ? '选持有人、' : ''}绑定项目、勾是否允许写入——<b>明文只展示一次</b>，当场复制</li>
                                            <li>端点默认为 <span className="font-mono">{effEndpoint}</span>（host 随访问地址自动推导；容器化/反代部署可在左栏改，HTTP Streamable，无状态 POST-only）</li>
                                            <li>把端点 + 令牌配到下方任意一种客户端 / 自己的 agent 里</li>
                                        </ol>
                                        <div className="mt-3 text-xs text-[#66738F] leading-relaxed">
                                            令牌绑定单项目（数据隔离）；只读令牌不暴露写入工具；限流：读 60 次/分、写 20 次/分。
                                            {bearer ? ' 已检测到当前选中令牌，下方示例已代入。' : ' 尚未选令牌，示例中用占位符表示，替换为你的明文即可。'}
                                        </div>
                                    </div>

                                    <div className="surface-card p-5 space-y-3">
                                        <SectionHead title="curl 调用" hint="命令行自测" />
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
                                        <SectionHead title="Python 调用" hint="官方 MCP SDK" />
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
                                        <SectionHead title="配到现成 MCP 客户端" hint="Cursor / Claude Code / mcp-remote" />
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

                                    <div className="surface-card p-5 space-y-3">
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                            <div>
                                                <SectionHead title="Agent Skill" extra={<TkTag tone="violet">推荐</TkTag>} />
                                                <div className="text-xs text-[#66738F] mt-1 leading-relaxed">
                                                    连接后读取 MCP 资源 <span className="font-mono">ontology://skill/ontology-retrieval</span>；
                                                    或复制完整包 <span className="font-mono">.dsh/skills/ontology-platform-retrieval/</span>（含
                                                    <span className="font-mono"> scripts/check.py、retrieve.py</span>），
                                                    harness 配置该 Skill 后即可按统一口径查图谱与检索结果。
                                                </div>
                                            </div>
                                            <Button size="small" icon={<CopyOutlined />}
                                                onClick={() => { navigator.clipboard.writeText(MCP_SKILL_TEXT); message.success('已复制 Skill'); }}>
                                                复制 Skill
                                            </Button>
                                        </div>
                                        <CodeBlock title="SKILL.md（给智能体 harness）" code={MCP_SKILL_TEXT} />
                                    </div>

                                    <div className="surface-card p-5">
                                        <SectionHead className="mb-3" title="注意事项" hint="无状态网关的行为边界" />
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
                        )}

                        {activeTab === 'tools' && (
                            <div className="grid gap-4 lg:h-[480px] lg:grid-cols-[minmax(0,300px)_minmax(0,1fr)] xl:h-auto xl:min-h-0 xl:flex-1">
                                <section className="surface-card flex min-h-0 min-w-0 flex-col overflow-hidden pb-3">
                                    <div className="flex items-center justify-between gap-2 border-b border-[#EDF1F8] px-4 py-2.5">
                                        <SectionHead
                                            title="工具清单"
                                            hint={<TkTag mono tone="quiet">{tools.length}</TkTag>}
                                        />
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
                                    <div className="border-b border-[#EDF1F8] px-4 py-2 tk-controls">
                                        <Input size="small" allowClear prefix={<SearchOutlined className="text-[#A9B4C9]" />}
                                            placeholder="搜索工具名称或描述" value={toolSearch}
                                            onChange={e => setToolSearch(e.target.value)} {...toolSearchInputProps} />
                                    </div>
                                    <div className="min-h-[220px] flex-1 overflow-auto p-2">
                                        {filteredTools.length === 0 ? (
                                            connected && toolSearchQuery ? (
                                                <EmptyState
                                                    compact
                                                    title="没有匹配的工具"
                                                    hint="换个关键词试试，或清除搜索"
                                                    actionText="清除搜索"
                                                    onAction={() => setToolSearch('')}
                                                />
                                            ) : (
                                                <EmptyState
                                                    compact
                                                    icon={<ToolOutlined />}
                                                    title={connected ? '还没有工具' : '尚未连接'}
                                                    hint={connected ? '点右上角刷新，重新拉取该令牌可见的工具' : '连接后展示该令牌可见的 MCP 工具'}
                                                    actionText={connected ? '刷新' : undefined}
                                                    onAction={connected ? () => { void listTools(); } : undefined}
                                                />
                                            )
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
                                                    className={`mb-1 w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                                                        selectedTool?.name === t.name
                                                            ? 'border-[rgba(91,141,239,0.40)] bg-[rgba(91,141,239,0.12)]'
                                                            : 'border-transparent hover:border-[#E7EEF9] hover:bg-[rgba(91,141,239,0.05)]'
                                                    }`}>
                                                    <div className="flex items-center justify-between gap-2">
                                                        <span className="truncate font-mono text-[13px] font-medium text-[#1F2A44]">{t.name}</span>
                                                        <RightOutlined className="shrink-0 text-[10px] text-[#A9B4C9]" />
                                                    </div>
                                                    <div className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-[#66738F]">{t.description}</div>
                                                </button>
                                            ))
                                        )}
                                    </div>
                                </section>

                                <div className="min-w-0 space-y-4 lg:min-h-0 lg:overflow-y-auto lg:pr-1">
                                    {!selectedTool ? (
                                        <section className="surface-card flex min-h-[320px] flex-col items-center justify-center px-6 text-center lg:h-full lg:min-h-0">
                                            <EmptyState
                                                compact
                                                icon={<ThunderboltOutlined />}
                                                title="选择一个工具"
                                                hint="从左侧清单选一个工具，查看参数并调用"
                                            />
                                        </section>
                                    ) : (
                                        <>
                                            <section className="surface-card p-4">
                                                <SectionHead
                                                    className="mb-2"
                                                    title="工具信息"
                                                    extra={<TkTag tone="quiet">{schemaProps.length} 个参数</TkTag>}
                                                />
                                                <div className="break-all font-mono text-[15px] font-semibold text-[#1F2A44]">{selectedTool.name}</div>
                                                <div className="mt-1 text-[13px] leading-relaxed text-[#66738F]">{selectedTool.description}</div>
                                            </section>

                                            <section className="surface-card p-4 tk-controls">
                                                <SectionHead
                                                    className="mb-3"
                                                    title="调用参数"
                                                    extra={<span className="text-[11px] text-[#8A94AB]">带 <span className="text-[#B42318]">*</span> 为必填</span>}
                                                />
                                                {schemaProps.length === 0 ? (
                                                    <div className="py-2 text-[13px] text-[#66738F]">该工具无入参，直接运行即可</div>
                                                ) : (
                                                    <div className="space-y-3">
                                                        {schemaProps.map(([key, prop]) => (
                                                            <div key={key}>
                                                                <div className="mb-1 flex flex-wrap items-baseline gap-2 text-xs">
                                                                    <span className="font-mono text-[#3D4B66]">{key}</span>
                                                                    {requiredKeys.includes(key) && <span className="text-[#B42318]">*</span>}
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
                                                                        className="w-full max-w-xl"
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
                                                <div className="mt-4 flex flex-wrap gap-2 border-t border-[#EDF1F8] pt-4">
                                                    <Button type="primary" className="tk-primary-btn" icon={<PlayCircleOutlined />} onClick={runTool} loading={calling}>
                                                        运行工具
                                                    </Button>
                                                    <Button className="tk-soft-btn" icon={<CopyOutlined />} onClick={copyInput}>Copy Input</Button>
                                                </div>
                                            </section>

                                            {callResult && (
                                                <section className="surface-card p-4">
                                                    <SectionHead
                                                        className="mb-2"
                                                        title="调用结果"
                                                        extra={calling
                                                            ? <TkTag tone="brand">运行中…</TkTag>
                                                            : <TkTag tone={callResult.ok ? 'brand' : 'danger'}>{callResult.ok ? '成功' : '失败'}</TkTag>}
                                                    />
                                                    <div className={calling ? 'opacity-55 transition-opacity' : 'transition-opacity'}>
                                                        <ResultBody text={callResult.text} />
                                                    </div>
                                                </section>
                                            )}
                                        </>
                                    )}
                                </div>
                            </div>
                        )}

                        {activeTab === 'resources' && (
                            <div className="grid gap-4 lg:h-[480px] lg:grid-cols-[minmax(0,300px)_minmax(0,1fr)] xl:h-auto xl:min-h-0 xl:flex-1">
                                <section className="surface-card flex min-h-0 min-w-0 flex-col overflow-hidden pb-3">
                                    <div className="flex items-center justify-between gap-2 border-b border-[#EDF1F8] px-4 py-2.5">
                                        <SectionHead
                                            title="资源清单"
                                            hint={<TkTag mono tone="quiet">{resources.length}</TkTag>}
                                        />
                                        <Tooltip title="刷新资源列表（List Resources）">
                                            <Button size="small" type="text" icon={<ReloadOutlined />}
                                                disabled={!connected} onClick={listResources} />
                                        </Tooltip>
                                    </div>
                                    <div className="min-h-[220px] flex-1 overflow-auto p-2">
                                        {resources.length === 0 ? (
                                            <EmptyState
                                                compact
                                                icon={<FolderOpenOutlined />}
                                                title={connected ? '还没有资源' : '尚未连接'}
                                                hint={connected ? '点右上角刷新，列出该项目的 MCP 资源' : '连接后可浏览该项目的 MCP 资源'}
                                                actionText={connected ? '刷新' : undefined}
                                                onAction={connected ? () => { void listResources(); } : undefined}
                                            />
                                        ) : (
                                            resources.map(r => (
                                                <button key={r.uri} type="button"
                                                    onClick={() => readResource(r)}
                                                    className={`mb-1 w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                                                        selectedResource?.uri === r.uri
                                                            ? 'border-[rgba(91,141,239,0.40)] bg-[rgba(91,141,239,0.12)]'
                                                            : 'border-transparent hover:border-[#E7EEF9] hover:bg-[rgba(91,141,239,0.05)]'
                                                    }`}>
                                                    <div className="flex items-center justify-between gap-2">
                                                        <span className="truncate text-[13px] font-medium text-[#1F2A44]">{r.name ?? r.uri}</span>
                                                        {r.mimeType && (
                                                            <Tooltip title={r.mimeType}>
                                                                <span className="flex shrink-0">
                                                                    <TkTag mono tone="quiet" className="max-w-[120px] truncate">{r.mimeType}</TkTag>
                                                                </span>
                                                            </Tooltip>
                                                        )}
                                                    </div>
                                                    <div className="mt-0.5 truncate font-mono text-[11px] text-[#66738F]">{r.uri}</div>
                                                    {r.description && <div className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-[#8A94AB]">{r.description}</div>}
                                                </button>
                                            ))
                                        )}
                                    </div>
                                </section>

                                <div className="min-w-0 space-y-4 lg:min-h-0 lg:overflow-y-auto lg:pr-1">
                                    {!selectedResource ? (
                                        <section className="surface-card flex min-h-[320px] flex-col items-center justify-center px-6 text-center lg:h-full lg:min-h-0">
                                            <EmptyState
                                                compact
                                                icon={<FolderOpenOutlined />}
                                                title="选择一个资源"
                                                hint="点击左侧资源，读取其内容"
                                            />
                                        </section>
                                    ) : (
                                        <>
                                            <section className="surface-card p-4">
                                                <SectionHead
                                                    className="mb-2"
                                                    title="资源信息"
                                                    extra={selectedResource.mimeType ? (
                                                        <Tooltip title={selectedResource.mimeType}>
                                                            <span className="flex max-w-[220px]">
                                                                <TkTag mono tone="quiet" className="truncate">{selectedResource.mimeType}</TkTag>
                                                            </span>
                                                        </Tooltip>
                                                    ) : undefined}
                                                />
                                                <div className="break-all text-[15px] font-medium text-[#1F2A44]">
                                                    {selectedResource.name ?? selectedResource.uri}
                                                </div>
                                                <div className="mt-1 break-all font-mono text-xs text-[#66738F]">{selectedResource.uri}</div>
                                            </section>
                                            <section className="surface-card p-4">
                                                <SectionHead
                                                    className="mb-2"
                                                    title="读取结果"
                                                    extra={resourceText ? (
                                                        <TkTag tone={resourceText.ok ? 'brand' : 'danger'}>{resourceText.ok ? '成功' : '失败'}</TkTag>
                                                    ) : undefined}
                                                />
                                                {readingRes ? (
                                                    <div className="py-2 text-[13px] text-[#8A94AB]">正在读取…</div>
                                                ) : resourceText ? (
                                                    <ResultBody text={resourceText.text} />
                                                ) : (
                                                    <div className="py-2 text-[13px] text-[#8A94AB]">即将自动读取…</div>
                                                )}
                                            </section>
                                        </>
                                    )}
                                </div>
                            </div>
                        )}

                        {activeTab === 'ping' && (
                            <div className="space-y-4 xl:flex xl:min-h-0 xl:flex-1 xl:flex-col xl:overflow-y-auto xl:pr-1">
                                {/* 检测卡撑满剩余高度（内容垂直居中），下面这张信息卡贴底，右栏下沿就与左卡齐平 */}
                                <section className="surface-card p-6 text-center xl:flex xl:flex-1 xl:flex-col xl:justify-center">
                                    <div
                                        className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl text-[22px] text-[#2F62D8]"
                                        style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                                    >
                                        <WifiOutlined />
                                    </div>
                                    <div className="mt-3 text-[15px] font-semibold text-[#1F2A44]">连通性检测</div>
                                    <div className="mt-1 text-[13px] leading-relaxed text-[#66738F]">
                                        向网关发送 JSON-RPC <span className="font-mono text-[#3D4B66]">ping</span>，验证令牌与端点是否可用
                                    </div>
                                    <div className="mt-5">
                                        <Button type="primary" className="tk-primary-btn px-5" icon={<LinkOutlined />} loading={pinging}
                                            onClick={doPing} disabled={!bearer}>
                                            发送 Ping
                                        </Button>
                                    </div>
                                    {/* 固定高度的状态位：未检测 / 检测中 / 结果都在同一行同一高度，
                                        点 Ping 时卡片内容不会再整体上下位移 */}
                                    <div className="mt-5 flex h-9 items-center justify-center">
                                        {pinging ? (
                                            <span className="inline-flex items-center gap-2 text-[12.5px] text-[#8A94AB]">
                                                <LoadingOutlined /> 检测中…
                                            </span>
                                        ) : pingResult ? (
                                            pingResult.ok ? (
                                                <TkTag tone="brand" className="!h-7 !px-3 !text-[12.5px]">
                                                    <CheckCircleFilled /> 连通正常 · 延迟 <b className="tk-mono">{pingResult.ms}</b> ms
                                                </TkTag>
                                            ) : (
                                                <Tooltip title={pingResult.msg}>
                                                    <span className="inline-flex max-w-full">
                                                        <TkTag tone="danger" className="!h-7 !px-3 !text-[12.5px]">
                                                            <CloseCircleFilled className="shrink-0" />
                                                            <span className="min-w-0 truncate">失败：{pingResult.msg}</span>
                                                        </TkTag>
                                                    </span>
                                                </Tooltip>
                                            )
                                        ) : (
                                            <span className="inline-flex items-center gap-2 text-[12px] text-[#A9B4C9]">
                                                <WifiOutlined className="text-[11px]" /> 尚未检测
                                            </span>
                                        )}
                                    </div>
                                    <div className="mt-5 text-[11px] text-[#8A94AB]">
                                        限流：读 60 次/分 · 写 20 次/分（每令牌）；认证失败返回 JSON-RPC error -32001
                                    </div>
                                </section>

                                <section className="surface-card grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
                                    <div className="min-w-0">
                                        <div className="mb-1 flex items-center gap-1.5 text-[11px] text-[#8A94AB]"><Dot tone="brand" />端点</div>
                                        <div className="break-all font-mono text-xs text-[#3D4B66]">{effEndpoint}</div>
                                    </div>
                                    <div className="min-w-0">
                                        <div className="mb-1 flex items-center gap-1.5 text-[11px] text-[#8A94AB]"><Dot tone="violet" />令牌</div>
                                        <div className="truncate text-xs text-[#3D4B66]">
                                            {selectedToken
                                                ? `${selectedToken.name}（${selectedToken.token_hint}）`
                                                : <span className="text-[#8A94AB]">未选择</span>}
                                        </div>
                                    </div>
                                </section>
                            </div>
                        )}
                    </div>
                </div>

                {/* ── 调用历史（全宽卡，默认收起） ── */}
                <section className="surface-card mt-4 overflow-hidden xl:shrink-0">
                    <div className="flex h-11 items-center justify-between gap-2 px-4">
                        <div className="flex min-w-0 items-center gap-2.5">
                            <span className="tk-mark" aria-hidden />
                            <span className="text-[13px] font-semibold text-[#1F2A44]">调用历史</span>
                            <TkTag mono tone="quiet">{history.length}</TkTag>
                        </div>
                        <div className="flex items-center gap-1.5">
                            {/* 清空压到和「展开历史」同一档字号/高度，靠颜色分主次：蓝=看，灰=清 */}
                            <Button
                                size="small"
                                type="text"
                                className="!h-6 !px-1.5 !text-[11px] !text-[#66738F] hover:!text-[#B42318]"
                                icon={<ClearOutlined />}
                                disabled={history.length === 0}
                                onClick={() => { setHistory([]); setExpandedHistory(null); }}
                            >
                                清空
                            </Button>
                            {/* 展开/收起沿用「本体问答 · 溯源引用」那支文字按钮：上下箭头 + 品牌蓝 + hover 淡底 */}
                            <button
                                type="button"
                                onClick={() => setHistoryOpen(o => !o)}
                                aria-expanded={historyOpen}
                                className="-mr-1 flex items-center gap-1 rounded-[7px] px-1.5 py-0.5 text-[11px] text-[#2A57C6] transition-colors hover:bg-[rgba(91,141,239,0.12)] hover:text-[#1F47A8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(91,141,239,0.35)]"
                            >
                                {historyOpen ? <UpOutlined /> : <DownOutlined />}
                                <span>{historyOpen ? '收起历史' : '展开历史'}</span>
                            </button>
                        </div>
                    </div>
                    {historyOpen && (
                        <div className="grid grid-cols-1 border-t border-[#EDF1F8] xl:grid-cols-[minmax(0,1fr)_280px]">
                            <div className="max-h-[280px] overflow-auto px-4 py-3">
                                {history.length === 0 ? (
                                    <EmptyState
                                        compact
                                        icon={<ApiOutlined />}
                                        title="暂无调用记录"
                                        hint="工具与资源的每次调用都会记在这里"
                                    />
                                ) : (
                                    history.map(h => (
                                        <div key={h.id} className="mb-1.5">
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
                                                        : <CloseCircleFilled className="text-[#B42318]" />}
                                                </span>
                                                <span className="text-[#A9B4C9] shrink-0">{h.at}</span>
                                            </button>
                                            {expandedHistory === h.id && (
                                                /* 展开体和上面那行标题留出间隔，并换成更浅的冷白底——
                                                   原来和选中行同为 #F7F9FD，两块会糊成一片 */
                                                <div className="mx-2 mt-1.5 mb-2 grid grid-cols-2 gap-3 rounded-lg border border-[#E7EEF9] bg-[#FBFDFF] p-2.5 font-mono text-[11px]">
                                                    <div className="min-w-0">
                                                        <div className="mb-1 flex items-center gap-1.5 text-[#66738F]">
                                                            <Dot tone="cyan" />请求
                                                        </div>
                                                        <div className="overflow-auto max-h-40"><JsonNode value={h.request} depth={2} /></div>
                                                    </div>
                                                    <div className="min-w-0">
                                                        <div className="mb-1 flex items-center gap-1.5 text-[#66738F]">
                                                            <Dot tone="amber" />响应
                                                        </div>
                                                        <div className="overflow-auto max-h-40">
                                                            {h.response ? <JsonNode value={h.response} depth={2} /> : <span className="text-[#A9B4C9]">-</span>}
                                                        </div>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    ))
                                )}
                            </div>
                            <div className="border-t border-[#EDF1F8] px-4 py-3 xl:border-l xl:border-t-0">
                                <SectionHead
                                    className="mb-2"
                                    title="服务端通知"
                                    extra={<TkTag mono tone="quiet">0</TkTag>}
                                />
                                <EmptyState
                                    compact
                                    icon={<ApiOutlined />}
                                    title="无服务端推送"
                                    hint="网关不会主动下发消息"
                                />
                            </div>
                        </div>
                    )}
                </section>
            </div>

                        {/* ── 签发令牌弹窗 */}
            <Modal
                title="签发 MCP 令牌" open={issueOpen} footer={null}
                onCancel={() => { setIssueOpen(false); setIssuedPlain(null); }}
            >
                {issuedPlain ? (
                    <div className="space-y-3 py-2">
                        <Note tone="amber" icon={<WarningOutlined />}>
                            明文仅此一次，请立即复制保存（平台只存 SHA-256，无法找回）
                        </Note>
                        <div className="flex gap-2">
                            <Input value={issuedPlain} readOnly className="font-mono !text-xs" />
                            <Button icon={<CopyOutlined />}
                                onClick={() => { navigator.clipboard.writeText(issuedPlain); message.success('已复制令牌'); }} />
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
                                <Select className="w-full" placeholder="选择用户" loading={pickerLoading}
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
                            <Select className="w-full" placeholder="选择项目" loading={pickerLoading}
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

/**
 * 构建器 · 文档解析 Tab（05 §6.4）：上传（拖拽/秒传 409 友好提示）→ 解析（参数抽屉）
 * → 分块列表（游标分页）→ 删除/下载。数据源：M3-1/M3-2 documents API。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
    Button, Card, Drawer, Modal, Popconfirm, Select, Space, Table, Tag, Tooltip, Upload, Input, message,
} from 'antd';
import {
    CloudUploadOutlined, DeleteOutlined, DownloadOutlined, FileTextOutlined,
    ReloadOutlined, ScissorOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

import { DocumentChunkRow, DocumentRow, documentsApi } from '../../../api/documents';

const { TextArea } = Input;

const STATUS_TAG: Record<string, { color: string; text: string }> = {
    uploaded: { color: 'default', text: '待解析' },
    parsing: { color: 'processing', text: '解析中' },
    parsed: { color: 'success', text: '已解析' },
    failed: { color: 'error', text: '失败' },
};

interface Props {
    projectId: number;
    /** 解析完成回调（供 BuilderPage 顶部提示刷新等） */
    onChanged?: () => void;
}

const DocumentsTab: React.FC<Props> = ({ projectId, onChanged }) => {
    const [docs, setDocs] = useState<DocumentRow[]>([]);
    const [loading, setLoading] = useState(false);
    const uploadingRef = useRef(false);
    const [parseDrawer, setParseDrawer] = useState<DocumentRow | null>(null);
    const [backend, setBackend] = useState('auto');
    const [chunkSize, setChunkSize] = useState('2000');
    const [chunksDoc, setChunksDoc] = useState<DocumentRow | null>(null);
    const [chunks, setChunks] = useState<DocumentChunkRow[]>([]);
    const [nextCursor, setNextCursor] = useState<number | null>(null);
    const [chunksLoading, setChunksLoading] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const r = await documentsApi.list(projectId);
            setDocs(r.documents || []);
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '加载文档列表失败');
        } finally {
            setLoading(false);
        }
    }, [projectId]);

    useEffect(() => { load(); }, [load]);

    // 解析中/上传中的文档每 3s 刷新，直到无活动任务
    useEffect(() => {
        if (!docs.some((d) => d.parse_status === 'parsing')) return;
        const t = setInterval(load, 3000);
        return () => clearInterval(t);
    }, [docs, load]);

    const handleUpload = async (files: File[]) => {
        if (files.length === 0) return;
        try {
            const r = await documentsApi.upload(projectId, files, true);
            message.success(r.message || `已上传 ${r.saved?.length ?? 0} 个文档，解析任务已派发`);
            load();
            onChanged?.();
        } catch (e: any) {
            const code = e.response?.data?.error?.code;
            if (code === 'DUPLICATE_UPLOAD') {
                Modal.info({
                    title: '秒传命中',
                    content: '相同内容的文档已存在于本项目，已直接复用，无需重复上传。',
                });
                load();
            } else {
                message.error(e.response?.data?.error?.message || e.response?.data?.detail || '上传失败');
            }
        }
    };

    const handleParse = async () => {
        if (!parseDrawer) return;
        try {
            const opts: any = { backend };
            if (chunkSize && Number(chunkSize) > 0) opts.chunk_size = Number(chunkSize);
            await documentsApi.parse(projectId, parseDrawer.id, opts);
            message.success('解析任务已派发');
            setParseDrawer(null);
            load();
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '派发解析失败');
        }
    };

    const openChunks = async (doc: DocumentRow) => {
        setChunksDoc(doc);
        setChunks([]);
        setNextCursor(null);
        setChunksLoading(true);
        try {
            const r = await documentsApi.chunks(projectId, doc.id);
            setChunks(r.chunks);
            setNextCursor(r.next_cursor);
        } finally {
            setChunksLoading(false);
        }
    };

    const loadMoreChunks = async () => {
        if (!chunksDoc || nextCursor == null) return;
        setChunksLoading(true);
        try {
            const r = await documentsApi.chunks(projectId, chunksDoc.id, nextCursor);
            setChunks((prev) => [...prev, ...r.chunks]);
            setNextCursor(r.next_cursor);
        } finally {
            setChunksLoading(false);
        }
    };

    const handleDelete = async (doc: DocumentRow) => {
        try {
            await documentsApi.remove(projectId, doc.id);
            message.success('已删除');
            load();
            onChanged?.();
        } catch (e: any) {
            message.error('删除失败');
        }
    };

    const columns: ColumnsType<DocumentRow> = [
        {
            title: '文档', dataIndex: 'filename', key: 'filename', ellipsis: true,
            render: (_, d) => (
                <Space>
                    <FileTextOutlined />
                    <span>{d.filename}</span>
                </Space>
            ),
        },
        {
            title: '状态', dataIndex: 'parse_status', key: 'parse_status', width: 100,
            render: (s: string, d) => (
                <Tooltip title={s === 'failed' ? (d.parse_error || '').slice(0, 300) : undefined}>
                    <Tag color={STATUS_TAG[s]?.color}>{STATUS_TAG[s]?.text || s}</Tag>
                </Tooltip>
            ),
        },
        { title: '页数', dataIndex: 'page_count', key: 'page_count', width: 70, render: (v) => v ?? '-' },
        { title: '语言', dataIndex: 'language', key: 'language', width: 80, render: (v) => v || '-' },
        { title: '分块', dataIndex: 'chunk_count', key: 'chunk_count', width: 80, render: (v) => v ?? 0 },
        { title: '解析器', dataIndex: 'parse_backend', key: 'parse_backend', width: 110, render: (v) => v || '-' },
        {
            title: '操作', key: 'actions', width: 240,
            render: (_, d) => (
                <Space>
                    <Button size="small" icon={<ScissorOutlined />}
                            disabled={d.parse_status === 'parsing'}
                            onClick={() => setParseDrawer(d)}>解析</Button>
                    <Button size="small" disabled={(d.chunk_count ?? 0) === 0}
                            onClick={() => openChunks(d)}>分块</Button>
                    <Tooltip title="下载原件">
                        <Button size="small" icon={<DownloadOutlined />}
                                disabled={!d.storage_key}
                                onClick={() => documentsApi.download(projectId, d.id, d.filename).catch(() => message.error('原件下载失败'))} />
                    </Tooltip>
                    <Popconfirm title="确认删除该文档？" onConfirm={() => handleDelete(d)}>
                        <Button size="small" danger icon={<DeleteOutlined />} />
                    </Popconfirm>
                </Space>
            ),
        },
    ];

    return (
        <Card
            title="文档解析"
            extra={
                <Space>
                    <Upload
                        multiple
                        showUploadList={false}
                        beforeUpload={() => false}
                        onChange={async ({ fileList }) => {
                            if (uploadingRef.current || fileList.length === 0) return;
                            uploadingRef.current = true;
                            try {
                                await handleUpload(fileList.map((f) => f.originFileObj).filter(Boolean) as File[]);
                            } finally {
                                uploadingRef.current = false;
                            }
                        }}
                    >
                        <Button type="primary" icon={<CloudUploadOutlined />}>上传文档（自动解析）</Button>
                    </Upload>
                    <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
                </Space>
            }
        >
            <Table
                rowKey="id"
                size="small"
                loading={loading}
                columns={columns}
                dataSource={docs}
                pagination={{ pageSize: 10, showSizeChanger: false }}
                locale={{ emptyText: '暂无文档；点击右上角上传（txt/pdf/docx/md/xlsx/csv/pptx，≤200MB）' }}
            />

            <Drawer
                title={`解析参数 · ${parseDrawer?.filename || ''}`}
                open={!!parseDrawer}
                onClose={() => setParseDrawer(null)}
                width={380}
                extra={<Button type="primary" onClick={handleParse}>派发解析</Button>}
            >
                <div className="space-y-4">
                    <div>
                        <div className="mb-1 text-gray-600">解析后端</div>
                        <Select
                            style={{ width: '100%' }}
                            value={backend}
                            onChange={setBackend}
                            options={[
                                { value: 'auto', label: 'auto（自动路由，扫描件走 OCR）' },
                                { value: 'docling', label: 'docling（文本 PDF/Office）' },
                                { value: 'docling_ocr', label: 'docling_ocr（扫描件 OCR）' },
                                { value: 'vl_model', label: 'vl_model（视觉模型）' },
                                { value: 'native', label: 'native（纯文本快速）' },
                            ]}
                        />
                    </div>
                    <div>
                        <div className="mb-1 text-gray-600">chunk_size（字符，默认 2000）</div>
                        <TextArea rows={1} value={chunkSize} onChange={(e) => setChunkSize(e.target.value)} />
                    </div>
                </div>
            </Drawer>

            <Drawer
                title={`分块列表 · ${chunksDoc?.filename || ''}`}
                open={!!chunksDoc}
                onClose={() => setChunksDoc(null)}
                width={560}
            >
                <div className="space-y-3">
                    {chunks.map((c) => (
                        <div key={c.chunk_index} className="border rounded p-2 bg-gray-50">
                            <div className="text-xs text-gray-500 mb-1">
                                #{c.chunk_index} · 字符 [{c.char_start ?? '?'}–{c.char_end ?? '?'}]
                                {c.token_count != null && ` · ~${c.token_count} tokens`}
                            </div>
                            <div style={{ whiteSpace: 'pre-wrap' }}>{c.text.length > 600
                                ? `${c.text.slice(0, 600)}…`
                                : c.text}</div>
                        </div>
                    ))}
                    {chunks.length === 0 && !chunksLoading && <div className="text-gray-400">无分块</div>}
                    {nextCursor != null && (
                        <Button block loading={chunksLoading} onClick={loadMoreChunks}>加载更多</Button>
                    )}
                </div>
            </Drawer>
        </Card>
    );
};

export default DocumentsTab;

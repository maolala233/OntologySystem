/**
 * 项目文档管理卡（2026-11 构建界面改版）：构建中/完成后展示已上传文档清单，
 * 支持下载原件、删除文档（删除后可一键重新构建——管线会按剩余文档重抽）。
 * 数据源 documentsApi；构建进行中删除仍允许（管线按当时快照跑，删除不影响当轮）。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Card, Empty, Popconfirm, Space, Spin, Table, Tag, Upload, message } from 'antd';
import {
    CloudDownloadOutlined, DeleteOutlined, UploadOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

import { documentsApi, DocumentRow } from '../../../api/documents';

const PARSE_TAG: Record<string, { color: string; text: string }> = {
    uploaded: { color: 'default', text: '待解析' },
    parsing: { color: 'processing', text: '解析中' },
    parsed: { color: 'success', text: '已解析' },
    failed: { color: 'error', text: '解析失败' },
};

interface Props {
    projectId: number;
    /** 变化回调（父级刷新统计） */
    onChanged?: () => void;
}

const ProjectDocsCard: React.FC<Props> = ({ projectId, onChanged }) => {
    const [docs, setDocs] = useState<DocumentRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [uploading, setUploading] = useState(false);
    // 请求代际：轮询/删除/上传并发时，只有最新一次列表响应可写状态——
    // 否则删除后，删除前在途的旧响应返回会把已删文档"复活"回界面（实测踩坑）
    const fetchGenRef = useRef(0);

    const fetchList = useCallback(async (withSpin = false): Promise<DocumentRow[]> => {
        const gen = ++fetchGenRef.current;
        if (withSpin) setLoading(true);
        try {
            const r = await documentsApi.list(projectId);
            if (gen !== fetchGenRef.current) return []; // 过期响应，丢弃（状态也不写）
            const rows = r.documents || [];
            setDocs(rows);
            return rows;
        } catch {
            return [];
        } finally {
            if (gen === fetchGenRef.current) setLoading(false);
        }
    }, [projectId]);

    const load = useCallback(() => fetchList(true), [fetchList]);

    useEffect(() => { load(); }, [load]);

    // 轮询解析状态：有文档在解析中时每 2.5s 刷新，全部稳定后停（90s 上限）
    const pollParse = useCallback(async () => {
        let rounds = 0;
        const timer = setInterval(async () => {
            rounds += 1;
            try {
                const rows = await fetchList();
                const busy = rows.some(
                    (d) => d.parse_status === 'parsing' || d.parse_status === 'uploaded');
                if (!busy || rounds > 36) {
                    clearInterval(timer);
                    onChanged?.();
                }
            } catch {
                clearInterval(timer);
            }
        }, 2500);
    }, [projectId, load, onChanged]);

    // 上传文档（可多选）：上传即同步解析（auto_parse=true）
    // antd 的 onChange 每选一个文件触发一次且携带累积 fileList——必须按 uid 去重，
    // 否则：①同一批文件被重复上传 N 份（实测一次选择产生 4 份拷贝）；
    // ②早前已删除的文件仍留在 antd 内部列表里，再选任何新文件都会把它原样
    // 重传——后端秒传判重不查已删记录，于是已删文档"复活"（新增一行，刷新也在）
    const seenUploadUids = useRef<Set<string>>(new Set());
    const handleUpload = async (options: any) => {
        const newItems = (options.fileList || []).filter((f: any) => !seenUploadUids.current.has(f.uid));
        newItems.forEach((f: any) => seenUploadUids.current.add(f.uid));
        const files = newItems
            .map((f: any) => f.originFileObj)
            .filter(Boolean) as File[];
        if (!files.length) return;
        setUploading(true);
        try {
            await documentsApi.upload(projectId, files, true);
            message.success(`已上传 ${files.length} 个文档，解析中…；抽取本体请点上方「一键构建」`);
            await load();
            onChanged?.();
            pollParse();
        } catch (e: any) {
            const err = e?.response?.data;
            message.error(err?.error?.message || err?.message || '上传失败，请稍后重试');
        } finally {
            setUploading(false);
        }
    };

    const columns: ColumnsType<DocumentRow> = [
        { title: '文档', dataIndex: 'filename', key: 'filename', ellipsis: true },
        { title: '解析状态', dataIndex: 'parse_status', key: 'parse_status', width: 100,
          render: (v: string) => {
              const m = PARSE_TAG[v] || PARSE_TAG.uploaded;
              return <Tag color={m.color}>{m.text}</Tag>;
          } },
        { title: '操作', key: 'ops', width: 160,
          render: (_, d) => (
              <Space size={0}>
                  <Button type="link" size="small" icon={<CloudDownloadOutlined />}
                          onClick={() => documentsApi.download(projectId, d.id, d.filename)}>
                      下载
                  </Button>
                  <Popconfirm
                      title="删除该文档？"
                      description="删除后可点「重新构建」让 AI 按剩余文档重新生成本体"
                      okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
                      onConfirm={async () => {
                          try {
                              await documentsApi.remove(projectId, d.id);
                              message.success('文档已删除');
                              load();
                              onChanged?.();
                          } catch {
                              message.error('删除失败');
                          }
                      }}>
                      <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                          删除
                      </Button>
                  </Popconfirm>
              </Space>
          ) },
    ];

    return (
        <Card size="small"
              title={`上传的文档（${docs.length}）`}
              extra={(
                  <Upload
                      multiple
                      showUploadList={false}
                      beforeUpload={() => false}
                      onChange={handleUpload}
                      accept=".txt,.md,.pdf,.doc,.docx,.xls,.xlsx,.csv,.ppt,.pptx,.html,.json"
                      disabled={uploading}>
                      <Button size="small" icon={<UploadOutlined />} loading={uploading}>
                          上传文档
                      </Button>
                  </Upload>
              )}>
            {loading ? (
                <div className="py-6 text-center"><Spin /></div>
            ) : docs.length === 0 ? (
                <Empty description="暂无文档" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
                <Table rowKey="id" size="small" columns={columns}
                       dataSource={docs}
                       pagination={docs.length > 8 ? { pageSize: 8, showSizeChanger: false } : false} />
            )}
            <div className="text-xs text-gray-400 mt-2 flex items-center gap-1">
                <UploadOutlined /> 可随时增删文档；上传即自动解析，抽取本体请点上方「一键构建」
            </div>
        </Card>
    );
};

export default ProjectDocsCard;

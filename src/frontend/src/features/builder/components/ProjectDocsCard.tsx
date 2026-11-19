/**
 * 项目文档管理卡（2026-11 构建界面改版）：构建中/完成后展示已上传文档清单，
 * 支持下载原件、删除文档（删除后可一键重新构建——管线会按剩余文档重抽）。
 * 数据源 documentsApi；构建进行中删除仍允许（管线按当时快照跑，删除不影响当轮）。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { message } from '../../../shared/feedback';
import { Button, Card, Popconfirm, Space, Spin, Table, Tag, Tooltip, Upload } from 'antd';
import type { UploadProps } from 'antd';
import {
    CloudDownloadOutlined, DeleteOutlined, FileTextOutlined, LockOutlined, UploadOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

import { documentsApi, DocumentRow } from '../../../api/documents';
import { UPLOAD_ACCEPT, UPLOAD_EXTS, MAX_UPLOAD_BYTES, MAX_UPLOAD_LABEL } from '../documents/uploadFormats';

// 解析状态标签：用冷调淡底代替 antd 的语义色（绿/蓝/红在一屏里过于抢眼）。
// 渲染时统一补 marginInlineEnd: 0——antd Tag 默认带 8px 右外边距，列居中后
// 这 8px 会把胶囊往左推，看起来"居中了但没完全居中"（2026-11-16）
const PARSE_TAG: Record<string, { style: React.CSSProperties; text: string }> = {
    uploaded: { style: { background: 'rgba(15,23,42,0.05)', color: '#8A94AB', borderColor: 'rgba(15,23,42,0.08)' }, text: '待解析' },
    parsing: { style: { background: 'rgba(242,185,80,0.20)', color: '#B8790F', borderColor: 'rgba(242,185,80,0.45)' }, text: '解析中' },
    parsed: { style: { background: 'rgba(91,141,239,0.12)', color: '#2A57C6', borderColor: 'rgba(91,141,239,0.28)' }, text: '已解析' },
    failed: { style: { background: 'rgba(180,35,24,0.10)', color: '#B42318', borderColor: 'rgba(180,35,24,0.22)' }, text: '解析失败' },
};

interface Props {
    projectId: number;
    /** 变化回调（父级刷新统计） */
    onChanged?: () => void;
    /** 当前管线运行阶段（来自 PipelinePanel 上抛）：解析完成后文档状态仍是「已解析」，
     *  用它把列显示成「骨架抽取中 / 实例抽取中 …」，避免构建中看起来一直卡在已解析 */
    pipelineStage?: { key: string; label: string; status: string; message?: string } | null;
    /** 构建进行中（running/queued）：后端拒绝改文档集合（409 PIPELINE_RUNNING），
     *  这里同步把上传/删除置灰并给出原因（2026-11-16，配合后端 c9b9388） */
    locked?: boolean;
}

const ProjectDocsCard: React.FC<Props> = ({ projectId, onChanged, pipelineStage, locked = false }) => {
    /** 后端 409 PIPELINE_RUNNING 翻成人话，其余走后端 message（2026-11-16） */
    const docErrorText = (e: any, fallback: string): string => {
        const err = e?.response?.data?.error;
        if (err?.code === 'PIPELINE_RUNNING') return '构建进行中，结束后才能修改文档';
        return err?.message || e?.response?.data?.message || fallback;
    };
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

    // 解析状态轮询（挂载即生效，不限场景）：有文档待解析/解析中时每 2.5s 刷新，
    // 全部落定后停并通知父级。旧版只在"本次上传后"轮询且 90s 封顶——页面在
    // 解析期间被刷新（或管线代为解析）就永远停在「解析中」；大文档解析超 90s
    // 也会被掐断（2026-11-08 实测）
    // 管线正在「解析文档」阶段时也必须轮询：重新构建（restart）会把已解析文档重新置为解析中，
    // 若本地缓存还停留在上一轮的「已解析」，不轮询就会一直显示已解析、看不到真实进度
    const pipelineParsing = pipelineStage?.key === 'parse' && pipelineStage.status === 'running';
    // 依赖里只能放「聚合出来的布尔值」，不能放 docs 数组：
    // fetchList 每次都 setDocs 生成新数组，若 docs 进依赖，effect 会被自己触发的渲染
    // 反复重启，而重启时又会立刻 fetchList 一次 → 死循环（实测构建期间按网络往返速度打接口）
    const docsBusy = docs.some((d) => d.parse_status === 'parsing' || d.parse_status === 'uploaded');
    useEffect(() => {
        const busy = pipelineParsing || docsBusy;
        if (!busy) return;
        if (pipelineParsing) void fetchList();  // 刚进入解析阶段先立刻拉一次，不等第一个 2.5s 周期
        // 构建中的解析是后台任务（用户在盯上面的进度面板），5s 一刷即可，一次大文档
        // 解析能省掉一半请求；用户自己上传触发的解析保持 2.5s，反馈要及时（2026-11-19）
        const t = setInterval(async () => {
            try {
                const rows = await fetchList();
                const stillBusy = rows.some(
                    (d) => d.parse_status === 'parsing' || d.parse_status === 'uploaded');
                if (!stillBusy) onChanged?.(); // 全部落定才通知父级刷新统计（避免每帧打 DB）
            } catch { /* 下一轮再试 */ }
        }, pipelineParsing ? 5000 : 2500);
        return () => clearInterval(t);
    }, [docsBusy, fetchList, onChanged, pipelineParsing]);

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
        } catch (e: any) {
            message.error(docErrorText(e, '上传失败，请稍后重试'));
        } finally {
            setUploading(false);
        }
    };

    const columns: ColumnsType<DocumentRow> = [
        // 列名用「文档名称」：卡片标题已经是「文档（N）」，表头再写「文档」会和标题重复，
        // 也说不清这一列装的是什么（2026-11-16）
        { title: '文档名称', dataIndex: 'filename', key: 'filename', ellipsis: true },
        // 默认展示文档自身的解析状态；只有「解析阶段正在解析这一份」时临时显示「解析中」——
        // 后端 run_parse 结束才落 parsed，本地缓存可能还是上一轮的 parsed（进入页面瞬间/轮询间隔），
        // 不兜这一下会出现「上面正在解析、下面已解析」（2026-11-11 实测）
        // 解析状态 / 操作两列统一居中（2026-11-16）：都是定宽的短内容列，
        // 左对齐会让胶囊和按钮贴着左边界、右边留一大块空白，读起来像没排满
        { title: '解析状态', dataIndex: 'parse_status', key: 'parse_status', width: 130, align: 'center' as const,
          render: (v: string, d) => {
              const parseMsg = pipelineStage?.key === 'parse' && pipelineStage.status === 'running'
                  ? String(pipelineStage.message || '')
                  : '';
              if (v === 'parsed' && parseMsg.startsWith('解析中') && d?.filename
                  && parseMsg.includes(d.filename)) {
                  return <Tag style={{ ...PARSE_TAG.parsing.style, marginInlineEnd: 0 }}>解析中</Tag>;
              }
              const m = PARSE_TAG[v] || PARSE_TAG.uploaded;
              return <Tag style={{ ...m.style, marginInlineEnd: 0 }}>{m.text}</Tag>;
          } },
        { title: '操作', key: 'ops', width: 190, align: 'center' as const,
          render: (_, d) => (
              // 两个链接按钮之间留 8px（2026-11-16）：原来 size={0}，图标几乎贴在一起，
              // 点击热区容易误触
              <Space size={8}>
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
                          } catch (e: any) {
                              message.error(docErrorText(e, '删除失败'));
                          }
                      }}>
                      <Tooltip title={locked ? '构建进行中，结束后可修改文档' : undefined}>
                          <span>
                              <Button type="link" size="small" danger icon={<DeleteOutlined />} disabled={locked}>
                                  删除
                              </Button>
                          </span>
                      </Tooltip>
                  </Popconfirm>
              </Space>
          ) },
    ];

    // 上传配置（2026-11-16）：段头按钮与空状态里的入口是同一个动作，抽出来复用，
    // 空状态因此不用再写"点击右上角…"这种指路文案，直接给可点的入口
    const uploadProps: UploadProps = {
        multiple: true,
        showUploadList: false,
        accept: UPLOAD_ACCEPT,
        disabled: uploading,
        beforeUpload: (f) => {
            const ext = String(f.name).split('.').pop()?.toLowerCase() || '';
            if (!UPLOAD_EXTS.includes(ext)) {
                message.warning(`不支持 .${ext} 格式：仅支持 ${UPLOAD_EXTS.join(' / ')}`);
                return Upload.LIST_IGNORE;
            }
            if (f.size > MAX_UPLOAD_BYTES) {
                message.warning(`「${f.name}」超过 ${MAX_UPLOAD_LABEL} 上限`);
                return Upload.LIST_IGNORE;
            }
            return false;
        },
        onChange: handleUpload,
    };

    return (
        <Card size="small"
              // 段标题不再挂实时阶段标签（2026-11-17 用户要求）：构建板块已经常驻显示
              // 「当前阶段：实体消解 · 用时」，这里再来一个「构建中 · 实体消解」是同一句话的第二遍
              title={<span>文档（{docs.length}）</span>}
              extra={(
                  <Upload {...uploadProps} disabled={uploading || locked}>
                      {/* 按钮文案跟状态走（2026-11-16）：已有文档时再说"上传文档"像是第一次操作，
                          改为"继续上传"——语义是往这个列表里再加一份，不是重新上传整个项目。
                          构建进行中禁用（后端会 409）：Tooltip 套一层 span 才接得到事件 */}
                      <Tooltip title={locked ? '构建进行中，结束后可修改文档' : undefined}>
                          <span>
                              {/* 上传是这个段的次要主操作（2026-11-17）：白底灰边压在冷蓝纸面上太素，
                                  改成和「查看图谱」同一枚淡蓝软底按钮——整页只留「发布」是实心蓝 */}
                              <Button size="small" type="primary" ghost icon={<UploadOutlined />}
                                      loading={uploading} disabled={locked}>
                                  {docs.length > 0 ? '继续上传' : '上传文档'}
                              </Button>
                          </span>
                      </Tooltip>
                  </Upload>
              )}>
            {loading ? (
                <div className="py-6 text-center"><Spin /></div>
            ) : docs.length === 0 ? (
                /* 保持原来的图标块 + 文案（2026-11-16 一行式版本观感更差，已回退），
                   只把上下留白从 py-8 收到 py-4：整块高度约 110px，不再空一大片 */
                <div className="flex flex-col items-center justify-center py-4">
                    <div
                        className="w-12 h-12 rounded-2xl flex items-center justify-center text-[20px] text-[#2F62D8]"
                        style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                    >
                        <FileTextOutlined />
                    </div>
                    <div className="mt-2.5 text-[13px] text-[#66738F]">暂无文档，点击右上角「上传文档」添加</div>
                </div>
            ) : (
                <Table rowKey="id" size="small" columns={columns}
                       dataSource={docs}
                       pagination={docs.length > 8 ? { pageSize: 8, showSizeChanger: false } : false} />
            )}
            <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-[#A3ADC2]">
                <span className="flex items-center gap-1">
                    <UploadOutlined />
                    <span>支持</span>
                    {/* 扩展名走等宽字形（2026-11-17）：一长串小写字母用比例字体看成一团，
                        等宽后能按「/」一眼数出支持哪些格式 */}
                    <span className="tk-mono text-[11px] text-[#8A94AB]">{UPLOAD_EXTS.join(' / ')}</span>
                </span>
                {locked && (
                    // 构建锁提示（2026-11-18）：原来和左侧「支持哪些格式」挤在同一行、
                    // 同一档字号，读起来像一句话被硬拼起来；改成右对齐的琥珀小胶囊，
                    // 带锁图标，和左侧说明拉开距离
                    <span className="ml-auto inline-flex items-center gap-1 rounded-md border px-2 py-[2px] text-[11px]"
                          style={{
                              color: '#B8790F',
                              borderColor: 'rgba(242,185,80,0.35)',
                              background: 'rgba(242,185,80,0.12)',
                          }}>
                        <LockOutlined style={{ fontSize: 11 }} />
                        构建进行中，暂不能上传或删除文档
                    </span>
                )}
            </div>
        </Card>
    );
};

export default ProjectDocsCard;

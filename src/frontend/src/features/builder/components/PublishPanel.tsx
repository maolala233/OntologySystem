/**
 * 发布检查面板（2026-11-05）：项目详情页「发布」阶段的内容区（替代独立页面/弹窗）。
 * 未发布：发布预检（影响分析 + 确认发布；未通过给 人话拦截原因）；
 * 已发布：版本信息（vN/时间/校验和）+ 多格式导出 + 取消发布 + 重新发布。
 * 发布时系统自动执行质量门控（结构机检 + 审核清零），未通过则旧版本继续生效。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { message } from '../../../shared/feedback';
import { Alert, Button, Card, Space, Spin, Table, Tag, Typography } from 'antd';
import { CloudUploadOutlined, ArrowLeftOutlined } from '@ant-design/icons';

import { ImpactSummary, impactApi } from '../../../api/cqs';
import { publishApi, VersionRow, versionsApi } from '../../../api/governance';
import { EXPORT_FORMAT_EXT, ExportFormatId, projectsApi } from '../../../api/projects';
import { pipelineApi } from '../../../api/pipeline';
import { publishWithDomain } from './publishFlow';

const { Text } = Typography;

interface ImpactLike {
    diff: Record<string, { added: number; removed: number; modified: number }>;
    changed_classes?: string[];
    has_baseline: boolean;
    affected: Record<string, number>;
    drift?: any[];
}
type ImpactSummaryAlias = ImpactLike;

const diffTotal = (impact: ImpactLike): number =>
    Object.values(impact.diff || {}).reduce(
        (acc, d) => acc + (d.added || 0) + (d.removed || 0) + (d.modified || 0), 0);

interface Props {
    projectId: number;
    /** 回到结果展示（步骤条回退） */
    onBackToResults: () => void;
}

const PublishPanel: React.FC<Props> = ({ projectId, onBackToResults }) => {
    const [loading, setLoading] = useState(true);
    const [publishing, setPublishing] = useState(false);
    const [isPublished, setIsPublished] = useState(false);
    const [version, setVersion] = useState<VersionRow | null>(null);
    const [impact, setImpact] = useState<ImpactSummary | null>(null);
    const [publishError, setPublishError] = useState<string | null>(null);
    const [blockedByReviews, setBlockedByReviews] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setPublishError(null);
        setBlockedByReviews(false);
        try {
            const s = await pipelineApi.buildSummary(projectId);
            setIsPublished(s.is_published);
        } catch { /* ignore */ }
        try {
            const v = await versionsApi.list(projectId);
            setVersion((v.items || []).find((x) => x.is_current) || null);
        } catch { /* ignore */ }
        try {
            setImpact(await impactApi.preview(projectId));
        } catch { /* 影响分析不可用不阻断 */ }
        setLoading(false);
    }, [projectId]);

    useEffect(() => { load(); }, [load]);

    const handlePublish = async () => {
        setPublishing(true);
        setPublishError(null);
        try {
            // 无知识域时弹选择器带域发布（2026-11-07：选域从构建期移到发布期）
            const r = await publishWithDomain(projectId);
            if (!r) return;
            const warn = (r as any).warnings?.length ? `（${(r as any).warnings.length} 条警告）` : '';
            message.success(`发布成功：v${r.version_no}${warn}`);
            await load();
        } catch (e: any) {
            const err = e.response?.data?.error || e.response?.data;
            if (err?.code === 'GATE_REJECTED') {
                const d = err.detail || {};
                setPublishError(
                    `质量门控未通过：胜 ${d.wins ?? '—'} / 平 ${d.ties ?? '—'} / 负 ${d.losses ?? '—'}，`
                    + `硬伤 ${d.hard_failures ?? '—'}。旧版本保持生效；修复图谱并重新落版后可重试。`);
            } else {
                const blockers = err?.detail?.blockers || [];
                if (blockers.length > 0) {
                    setPublishError(`还有 ${blockers.length} 个审核结果未清零，暂不能发布（AI 裁决进行中，稍后重试）。`);
                    setBlockedByReviews(true);
                } else {
                    setPublishError(err?.message || '发布失败，请检查发布条件（知识域、审核清零、本体结构机检、证据闭合）');
                    setBlockedByReviews(false);
                }
            }
        } finally {
            setPublishing(false);
        }
    };

    const handleUnpublish = async () => {
        try {
            await publishApi.unpublish(projectId);
            message.success('已取消发布（资产中心不再展示）');
            await load();
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '取消发布失败');
        }
    };

    const diffRows = impact ? [
        { key: 'classes', label: '类', ...(impact.diff.classes) },
        { key: 'properties', label: '属性', ...(impact.diff.properties) },
        { key: 'instances', label: '实例', ...(impact.diff.instances) },
        { key: 'relations', label: '关系', ...(impact.diff.relations) },
    ] : [];

    if (loading) {
        return <div className="py-10 text-center"><Spin /></div>;
    }

    return (
        <div className="space-y-4">
            {/* 阶段头：返回结果展示 */}
            <div className="flex items-center justify-between">
                <Button icon={<ArrowLeftOutlined />} onClick={onBackToResults}>
                    回到结果展示
                </Button>
                {isPublished && <Tag color="purple">已发布到资产中心</Tag>}
            </div>

            {/* 导出（不依赖发布状态，随时可下载） */}
            <Card size="small" title="导出">
                <div className="flex items-center gap-2 flex-wrap">
                    {(Object.keys(EXPORT_FORMAT_EXT) as ExportFormatId[]).map((fmt) => (
                        <Button key={fmt} size="small"
                                onClick={() => projectsApi.exportProject(projectId, fmt)}>
                            {EXPORT_FORMAT_EXT[fmt].toUpperCase()}
                        </Button>
                    ))}
                </div>
            </Card>

            {/* 已发布：版本信息 + 取消发布/重新发布 */}
            {isPublished && (
                <Card size="small" title="当前发布版本"
                      extra={(
                          <Space>
                              <Button onClick={handleUnpublish}>取消发布</Button>
                              <Button type="primary" ghost icon={<CloudUploadOutlined />}
                                      onClick={handlePublish} loading={publishing}>
                                  重新发布（发新版本）
                              </Button>
                          </Space>
                      )}>
                {version ? (
                    <div className="text-sm text-gray-600">
                        版本 <b>v{version.version_no}</b>
                        {version.label && `（${version.label}）`}
                        {version.created_at && <> · 发布于 {new Date(version.created_at).toLocaleString('zh-CN')}</>}
                        {version.checksum && <> · 校验和 <Text code className="text-[11px]">{version.checksum.slice(0, 12)}…</Text></>}
                    </div>
                ) : (
                    <div className="text-sm text-gray-500">版本信息加载中…</div>
                )}
            </Card>
            )}

            {/* 发布检查：与上次发布版本对比 + 确认发布 */}
            <Card size="small" title="与上次发布版本的对比">
                {publishError && (
                    <Alert type="error" showIcon className="mb-3" message="发布被拦截"
                           description={(
                               <div className="space-y-2">
                                   <div>{publishError}</div>
                                   {blockedByReviews && (
                                       <span className="text-xs text-gray-500">
                                           系统会在 AI 裁决完成后自动清零，届时回到本面板重试即可。
                                       </span>
                                   )}
                               </div>
                           )} />
                )}
                {!impact && (
                    <div className="text-gray-500 text-sm mb-3">
                        对比数据不可用（首次发布将建立基线版本 v1，可直接发布）
                    </div>
                )}
                {impact && (
                    <div className="mb-3">
                        {!impact.has_baseline ? (
                            <div className="text-sm text-gray-600 mb-2">
                                首次发布：当前状态将建立基线版本 <b>v1</b>。
                            </div>
                        ) : (
                            <div className="text-sm text-gray-600 mb-2">
                                对比基线：上次发布版本 {version ? <b>v{version.version_no}</b> : ''}
                                {diffTotal(impact) === 0
                                    ? '，自该版本以来无变化。'
                                    : `，自该版本以来有 ${diffTotal(impact)} 处变化：`}
                            </div>
                        )}
                        <table className="w-full text-sm border rounded">
                            <thead>
                                <tr className="bg-gray-50 text-gray-600">
                                    <th className="text-left px-3 py-1.5 font-normal">对象</th>
                                    <th className="text-right px-3 py-1.5 font-normal">新增</th>
                                    <th className="text-right px-3 py-1.5 font-normal">删除</th>
                                    <th className="text-right px-3 py-1.5 font-normal">变化</th>
                                </tr>
                            </thead>
                            <tbody>
                                {diffRows.map((r) => (
                                    <tr key={r.key} className="border-t">
                                        <td className="px-3 py-1.5">{r.label}</td>
                                        <td className="px-3 py-1.5 text-right text-green-600">+{r.added}</td>
                                        <td className="px-3 py-1.5 text-right text-red-600">−{r.removed}</td>
                                        <td className="px-3 py-1.5 text-right text-amber-600">~{r.modified}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        {(impact.changed_classes || []).length > 0 && (
                            <div className="mt-2 text-xs text-gray-500">
                                变化的类：
                                {(impact.changed_classes || []).slice(0, 10).map((c: string, i: number) => (
                                    <Tag key={i} className="mr-1 mb-1" color="blue">{c}</Tag>
                                ))}
                                {(impact.changed_classes || []).length > 10 && ` 等 ${impact.changed_classes.length} 个`}
                            </div>
                        )}
                        {isPublished && diffTotal(impact) > 0 && (
                            <Alert type="info" showIcon className="mt-2"
                                   message="当前工作区与已发布版本存在差异"
                                   description={`资产中心展示的仍是已发布版本 ${version ? `v${version.version_no}` : ''} 的快照；点上方「重新发布（发新版本）」才会更新公共区内容。`} />
                        )}
                    </div>
                )}
                <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-400">
                        <CloudUploadOutlined className="mr-1" />
                        质量自检通过即可直接发布；发布将生成版本快照，可随时取消发布。
                    </span>
                    {!isPublished && (
                        <Button type="primary" size="large" loading={publishing}
                                icon={<CloudUploadOutlined />} onClick={handlePublish}>
                            确认发布
                        </Button>
                    )}
                </div>
            </Card>
        </div>
    );
};

export default PublishPanel;

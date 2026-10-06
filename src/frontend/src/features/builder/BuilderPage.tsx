/**
 * 构建器薄壳（05 §7）：路由参数 + Tab 容器 + 项目数据预取。
 * 四 Tab：文档解析 / 骨架编辑 / 实例探索 / 时间轴（URL 可分享，05 §2）。
 * 画布交互全部在 SchemaTab（迁移自原 3587 行 OntologyBuilderPage，逻辑不动）。
 */
import React, { lazy, Suspense, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Spin, Tabs, Tag, Tooltip } from 'antd';
import {
    ArrowLeftOutlined, CloudUploadOutlined, DatabaseOutlined,
    FieldTimeOutlined, NodeIndexOutlined,
} from '@ant-design/icons';

import { useBuilderStore } from './store';
import { confirmLeaveUnsaved } from '../../utils/unsavedGuard';

const DocumentsTab = lazy(() => import('./documents/DocumentsTab'));
const SchemaTab = lazy(() => import('./schema/SchemaTab'));
const GraphTab = lazy(() => import('./graph/GraphTab'));
const TimelineTab = lazy(() => import('./timeline/TimelineTab'));

const STATUS_TAG: Record<string, { color: string; text: string }> = {
    draft: { color: 'default', text: '草稿' },
    building: { color: 'processing', text: '构建中' },
    ready: { color: 'green', text: '就绪' },
    published: { color: 'purple', text: '已发布·只读' },
};

const BuilderPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();
    const pid = Number(projectId);
    const { projectName, projectStatus, domainName, loading, loadError, loadProject } = useBuilderStore();

    useEffect(() => {
        if (Number.isFinite(pid) && pid > 0) {
            loadProject(pid);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pid]);

    const activeTab = ['documents', 'schema', 'graph', 'timeline']
        .find((t) => location.pathname.endsWith(`/${t}`)) || 'documents';

    if (!Number.isFinite(pid) || pid <= 0) {
        return (
            <div className="h-screen flex items-center justify-center text-gray-500">
                缺少项目 ID，请从「我的项目」进入构建器。
            </div>
        );
    }
    if (loadError) {
        return (
            <div className="h-screen flex flex-col items-center justify-center gap-3 text-gray-500">
                <span>项目加载失败（不存在或无权限）</span>
                <Button onClick={() => navigate('/my-projects')}>返回我的项目</Button>
            </div>
        );
    }

    const tabItems = [
        {
            key: 'documents',
            label: <span><CloudUploadOutlined className="mr-1" />文档解析</span>,
            children: <DocumentsTab projectId={pid} onChanged={() => loadProject(pid)} />,
        },
        {
            key: 'schema',
            label: <span><NodeIndexOutlined className="mr-1" />骨架编辑</span>,
            children: <SchemaTab projectId={String(pid)} />,
        },
        {
            key: 'graph',
            label: <span><DatabaseOutlined className="mr-1" />实例探索</span>,
            children: <GraphTab projectId={pid} onChanged={() => loadProject(pid)} />,
        },
        {
            key: 'timeline',
            label: <span><FieldTimeOutlined className="mr-1" />时间轴</span>,
            children: <TimelineTab projectId={pid} onChanged={() => loadProject(pid)} />,
        },
    ];

    const statusMeta = STATUS_TAG[projectStatus] || STATUS_TAG.draft;

    return (
        <div className="h-screen flex flex-col bg-gray-50 overflow-hidden">
            {/* 头部：返回 + 项目信息 + 状态徽标（05 §6.4 Tab 栏 + 项目状态徽标） */}
            <div className="flex items-center gap-3 px-4 py-2 bg-white border-b border-gray-200 flex-shrink-0">
                <Button
                    icon={<ArrowLeftOutlined />}
                    size="small"
                    onClick={() => confirmLeaveUnsaved(() => navigate('/my-projects'))}
                >
                    返回
                </Button>
                <Spin spinning={loading} size="small">
                    <div className="flex items-center gap-2">
                        <span className="font-semibold text-gray-800">{projectName || `项目 #${pid}`}</span>
                        <Tooltip title={projectStatus === 'published'
                            ? '发布态只读：写操作需先取消发布（PUBLISHED_READONLY）'
                            : `状态：${projectStatus}`}>
                            <Tag color={statusMeta.color}>{statusMeta.text}</Tag>
                        </Tooltip>
                        {domainName && <Tag color="blue">{domainName}</Tag>}
                    </div>
                </Spin>
            </div>

            <Suspense fallback={<div className="p-10 text-center text-gray-400"><Spin /> 模块加载中…</div>}>
                <Tabs
                    activeKey={activeTab}
                    onChange={(key) => confirmLeaveUnsaved(() => navigate(`/projects/${pid}/${key}`))}
                    items={tabItems}
                    className="flex-1 px-4 pb-2 overflow-auto builder-tabs"
                    destroyInactiveTabPane={false}
                />
            </Suspense>
        </div>
    );
};

export default BuilderPage;

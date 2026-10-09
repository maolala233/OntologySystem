/**
 * ModelPicker - 构建器内的模型快捷切换（M2，docs/design/05 §6.10）
 * 展示抽取模型（优先本项目配置，普通用户也可见全局默认项），
 * 设默认仅限有权限的行：admin 任意；普通用户仅自己拥有的项目级配置
 * （后端 _scope_guard：global→admin，project→owner）。
 * 管理入口跳转 /admin/model-configs（普通用户可管理自己的项目级配置）。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Button, Dropdown, Tag, message } from 'antd';
import { ApiOutlined, SettingOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { modelConfigsApi, ModelConfigRow } from '../../api/model-configs';
import { projectsApi } from '../../api/projects';
import { useAuthStore } from './authStore';

interface Props {
    /** 当前项目 id：提供时拉取该项目的作用域配置并作为设默认的落点 */
    projectId?: number;
}

const ModelPicker: React.FC<Props> = ({ projectId }) => {
    const navigate = useNavigate();
    const { user } = useAuthStore();
    const isAdmin = user?.role === 'admin';
    const [rows, setRows] = useState<ModelConfigRow[]>([]);
    const [myProjectIds, setMyProjectIds] = useState<number[]>([]);
    const [loading, setLoading] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            const lists: Promise<ModelConfigRow[]>[] = [
                modelConfigsApi.list({ purpose: 'extract', scope: 'global' }),
            ];
            if (projectId) {
                lists.push(modelConfigsApi.list({ purpose: 'extract', scope: 'project', project_id: projectId }));
            }
            const [globalRs, projectRs] = await Promise.all([
                lists[0],
                lists[1] || Promise.resolve([] as ModelConfigRow[]),
            ]);
            setRows([...projectRs, ...globalRs]);
            if (!isAdmin) {
                const mine = await projectsApi.getMyProjects().catch(() => []);
                setMyProjectIds(mine.map((p) => p.id));
            }
        } catch {
            // 静默：工具条组件不打断主流程
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, [projectId, isAdmin]);

    const canSetDefault = (r: ModelConfigRow) =>
        isAdmin || (r.scope === 'project' && !!r.project_id && myProjectIds.includes(r.project_id));

    const items = useMemo(() => {
        const toChild = (r: ModelConfigRow) => ({
            key: `${r.scope}-${r.id}`,
            disabled: !canSetDefault(r),
            label: (
                <span>
                    {r.scope === 'project' ? '【本项目】' : '【全局】'}
                    {r.name} <Tag style={{ marginRight: 0 }}>{r.model_name}</Tag>
                    {r.is_default && <Tag color="green">默认</Tag>}
                </span>
            ),
        });
        const projectRows = rows.filter((r) => r.scope === 'project');
        const globalRows = rows.filter((r) => r.scope === 'global');
        const children: any[] = [];
        if (projectRows.length) {
            children.push({ key: 'group-project', type: 'group' as const, label: '本项目抽取模型', children: projectRows.map(toChild) });
        }
        if (globalRows.length) {
            children.push({ key: 'group-global', type: 'group' as const, label: '全局抽取模型', children: globalRows.map(toChild) });
        }
        if (!children.length) {
            children.push({ key: 'empty', type: 'group' as const, label: '暂无抽取模型配置', children: [] });
        }
        children.push(
            { type: 'divider' as const },
            { key: 'manage', icon: <SettingOutlined />, label: '管理模型配置…' },
        );
        return children;
    }, [rows, myProjectIds, isAdmin]);

    // 项目级默认优先于全局默认展示
    const defaultExtract =
        rows.find((r) => r.scope === 'project' && r.is_default) ||
        rows.find((r) => r.scope === 'global' && r.is_default);

    const handleClick = async ({ key }: { key: string }) => {
        if (key === 'manage') {
            navigate('/admin/model-configs');
            return;
        }
        if (key === 'empty') return;
        const row = rows.find((r) => `${r.scope}-${r.id}` === key);
        if (!row) return;
        try {
            await modelConfigsApi.setDefault(row.id);
            message.success(`已将「${row.name}」设为抽取默认模型`);
            load();
        } catch (err: any) {
            message.error(err.response?.data?.error?.message || '切换失败');
        }
    };

    return (
        <Dropdown
            menu={{ items, onClick: handleClick as any }}
            trigger={['click']}
            disabled={loading}
        >
            <Button size="small" icon={<ApiOutlined />} loading={loading}>
                {defaultExtract ? `${defaultExtract.model_name}` : '模型未配置'}
            </Button>
        </Dropdown>
    );
};

export default ModelPicker;

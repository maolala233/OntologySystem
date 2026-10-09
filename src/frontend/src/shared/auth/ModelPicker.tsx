/**
 * ModelPicker - 构建器内的模型快捷切换（M2，docs/design/05 §6.10，R14 起支持普通用户）
 * 展示抽取模型（优先本项目配置；全局配置普通用户也可选）。
 * 点击行为按角色：
 *  - admin：设项目级/全局级默认（后端 _scope_guard：global→admin，project→owner）；
 *  - 普通用户：点全局行 = 设为「我的抽取模型」（user_model_picks，仅全局配置、对所有项目生效；
 *    抽取任务按 请求显式→个人选择→项目默认→全局默认 解析）；
 *    自己拥有的项目级配置仍可设项目默认；「恢复默认」清除个人选择。
 * 管理入口跳转 /admin/model-configs（普通用户可管理自己的项目级配置）。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Button, Dropdown, Tag, message } from 'antd';
import { ApiOutlined, RollbackOutlined, SettingOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { modelConfigsApi, ModelConfigRow, ModelPickState } from '../../api/model-configs';
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
    const [pick, setPick] = useState<ModelPickState | null>(null);
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
            const [globalRs, projectRs, myPick] = await Promise.all([
                lists[0],
                lists[1] || Promise.resolve([] as ModelConfigRow[]),
                modelConfigsApi.getMyPick('extract').catch(() => null),
            ]);
            setRows([...projectRs, ...globalRs]);
            setPick(myPick);
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

    const ownedByMe = (r: ModelConfigRow) =>
        r.scope === 'project' && !!r.project_id && myProjectIds.includes(r.project_id);

    const items = useMemo(() => {
        const toChild = (r: ModelConfigRow) => {
            const mine = !!pick && pick.config_id === r.id;
            // 非 admin：全局行随时可点（设为个人选择）；项目行仅本人拥有可设默认
            const disabled = isAdmin ? false : (r.scope === 'project' && !ownedByMe(r));
            return {
                key: `${r.scope}-${r.id}`,
                disabled,
                label: (
                    <span>
                        {r.scope === 'project' ? '【本项目】' : '【全局】'}
                        {r.name} <Tag style={{ marginRight: 0 }}>{r.model_name}</Tag>
                        {mine && <Tag color="blue" style={{ marginLeft: 4 }}>我的选择</Tag>}
                        {r.is_default && !mine && <Tag color="green" style={{ marginLeft: 4 }}>默认</Tag>}
                    </span>
                ),
            };
        };
        const projectRows = rows.filter((r) => r.scope === 'project');
        const globalRows = rows.filter((r) => r.scope === 'global');
        const children: any[] = [];
        if (projectRows.length) {
            children.push({ key: 'group-project', type: 'group' as const, label: '本项目抽取模型', children: projectRows.map(toChild) });
        }
        if (globalRows.length) {
            children.push({
                key: 'group-global',
                type: 'group' as const,
                label: isAdmin ? '全局抽取模型' : '全局抽取模型（点击设为我的）',
                children: globalRows.map(toChild),
            });
        }
        if (!children.length) {
            children.push({ key: 'empty', type: 'group' as const, label: '暂无抽取模型配置', children: [] });
        }
        if (!isAdmin && pick) {
            children.push(
                { type: 'divider' as const },
                { key: 'reset-pick', icon: <RollbackOutlined />, label: '恢复默认（跟随项目/全局默认）' },
            );
        }
        children.push(
            { type: 'divider' as const },
            { key: 'manage', icon: <SettingOutlined />, label: '管理模型配置…' },
        );
        return children;
    }, [rows, pick, myProjectIds, isAdmin]);

    // 展示优先级：我的选择 → 项目默认 → 全局默认
    const pickedRow = rows.find((r) => pick && pick.config_id === r.id);
    const defaultExtract =
        rows.find((r) => r.scope === 'project' && r.is_default) ||
        rows.find((r) => r.scope === 'global' && r.is_default);
    const effective = pickedRow || defaultExtract;

    const handleClick = async ({ key }: { key: string }) => {
        if (key === 'manage') {
            navigate('/admin/model-configs');
            return;
        }
        if (key === 'empty') return;
        if (key === 'reset-pick') {
            try {
                await modelConfigsApi.clearMyPick('extract');
                setPick({ purpose: 'extract', config_id: null, config: null });
                message.success('已恢复默认：抽取将跟随项目/全局默认');
            } catch (err: any) {
                message.error(err.response?.data?.error?.message || '恢复默认失败');
            }
            return;
        }
        const row = rows.find((r) => `${r.scope}-${r.id}` === key);
        if (!row) return;
        try {
            if (isAdmin) {
                await modelConfigsApi.setDefault(row.id);
                // 清掉旧个人选择，避免它遮蔽刚设的默认（admin 也走 pick 解析）
                if (pick) modelConfigsApi.clearMyPick('extract').catch(() => {});
                message.success(`已将「${row.name}」设为抽取默认模型`);
            } else if (row.scope === 'global') {
                await modelConfigsApi.setMyPick(row.id, 'extract');
                message.success(`已选择「${row.name}」作为我的抽取模型`);
            } else {
                await modelConfigsApi.setDefault(row.id);
                message.success(`已将「${row.name}」设为抽取默认模型`);
            }
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
                {pickedRow ? `我的 ${pickedRow.model_name}` : effective ? effective.model_name : '模型未配置'}
            </Button>
        </Dropdown>
    );
};

export default ModelPicker;

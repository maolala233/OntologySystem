/**
 * ModelPicker - 构建器内的模型快捷切换（M2，docs/design/05 §6.10）
 * 只展示抽取模型默认项，可一键切换默认（admin / 项目 owner）；
 * 对话模型在问答页与 /admin/model-configs 管理，不在此混列。
 * 管理入口跳转 /admin/model-configs。
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Button, Dropdown, Tag, message } from 'antd';
import { ApiOutlined, SettingOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { modelConfigsApi, ModelConfigRow } from '../../api/model-configs';

const ModelPicker: React.FC = () => {
    const navigate = useNavigate();
    const [rows, setRows] = useState<ModelConfigRow[]>([]);
    const [loading, setLoading] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            setRows(await modelConfigsApi.list({ purpose: 'extract' }));
        } catch {
            // 静默：工具条组件不打断主流程
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const items = useMemo(() => {
        return [
            {
                key: 'group-extract',
                type: 'group' as const,
                label: '抽取模型',
                children: rows.map((r) => ({
                    key: String(r.id),
                    label: (
                        <span>
                            {r.name} <Tag style={{ marginRight: 0 }}>{r.model_name}</Tag>
                            {r.is_default && <Tag color="green">默认</Tag>}
                        </span>
                    ),
                })),
            },
            { type: 'divider' as const },
            { key: 'manage', icon: <SettingOutlined />, label: '管理模型配置…' },
        ];
    }, [rows]);

    const defaultExtract = rows.find((r) => r.purpose === 'extract' && r.is_default);

    const handleClick = async ({ key }: { key: string }) => {
        if (key === 'manage') {
            navigate('/admin/model-configs');
            return;
        }
        const row = rows.find((r) => String(r.id) === key);
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

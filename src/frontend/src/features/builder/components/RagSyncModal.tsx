/**
 * RAGFlow 同步弹窗（自骨架编辑迁入，实例探索与骨架编辑共用入口）：
 * 保存注入配置 → 获取知识库列表 → 将当前本体图谱注入 RAGFlow。
 */
import React, { useState } from 'react';
import { Button, Form, Input, Modal, Select, message } from 'antd';
import { InfoCircleOutlined } from '@ant-design/icons';

import { projectsApi } from '../../../api/projects';

interface Props {
    projectId: number;
    open: boolean;
    onClose: () => void;
}

const RagSyncModal: React.FC<Props> = ({ projectId, open, onClose }) => {
    const [injectForm] = Form.useForm();
    const [injecting, setInjecting] = useState(false);
    const [fetchingRagflow, setFetchingRagflow] = useState(false);
    const [ragflowDatasets, setRagflowDatasets] = useState<{ id: string; name: string }[]>([]);

    const handleOpen = async () => {
        try {
            const res = await projectsApi.getInjectConfig(projectId);
            const config = res.data || {};
            const isMasked = (v: string) => v && (v === '******' || v.includes('****'));
            injectForm.setFieldsValue({
                ragflow_host: config.ragflow_host || 'http://localhost:9380',
                ragflow_api_key: isMasked(config.ragflow_api_key) ? '' : (config.ragflow_api_key || ''),
                kb_id: config.kb_id || '',
            });
        } catch {
            injectForm.setFieldsValue({ ragflow_host: 'http://localhost:9380' });
        }
    };

    const handleSaveInjectConfig = async () => {
        try {
            const values = await injectForm.validateFields();
            await projectsApi.saveInjectConfig(projectId, values);
            message.success('注入配置已保存');
        } catch (error: any) {
            message.error(error.response?.data?.detail || '保存配置失败');
        }
    };

    const handleInjectToRagflow = async () => {
        setInjecting(true);
        try {
            await handleSaveInjectConfig();
            const res = await projectsApi.injectToRagflow(projectId);
            if (res.status === 'success') {
                const data = res.data;
                message.success(`注入成功！实体=${data.entities_created}，关系=${data.relations_created}，图谱=${data.graph_updated ? '已更新' : '未更新'}，类型映射=${data.ty2ents_updated ? '已更新' : '未更新'}`);
                onClose();
            } else {
                message.error(res.message || '注入失败');
            }
        } catch (error: any) {
            message.error(error.response?.data?.detail || '注入失败');
        } finally {
            setInjecting(false);
        }
    };

    const handleFetchRagflowInfo = async () => {
        const ragflowHost = injectForm.getFieldValue('ragflow_host')?.trim();
        const ragflowApiKey = injectForm.getFieldValue('ragflow_api_key')?.trim();
        if (!ragflowHost || !ragflowApiKey) {
            message.warning('请先填写 RAGFlow 地址和 API Key');
            return;
        }
        setFetchingRagflow(true);
        try {
            const res = await projectsApi.ragflowFetchInfo(projectId, ragflowHost, ragflowApiKey);
            if (res.status === 'success') {
                const datasets = res.datasets || [];
                const dsList = datasets.map((ds: any) => ({ id: ds.id, name: ds.name || ds.id }));
                setRagflowDatasets(dsList);
                if (dsList.length === 1) {
                    injectForm.setFieldsValue({ kb_id: dsList[0].id });
                }
                message.success(`获取成功！知识库: ${dsList.length}个`);
            } else {
                message.error(res.message || '获取RAGFlow信息失败');
            }
        } catch (error: any) {
            message.error(error.response?.data?.detail || '获取RAGFlow信息失败');
        } finally {
            setFetchingRagflow(false);
        }
    };

    return (
        <Modal
            title={<div className="flex items-center gap-2"><InfoCircleOutlined className="text-orange-500" /><span>RAG 同步（RAGFlow）</span></div>}
            open={open}
            afterOpenChange={(visible) => { if (visible) handleOpen(); }}
            onCancel={onClose}
            width={580}
            maskClosable={false}
            footer={[
                <Button key="cancel" onClick={onClose}>取消</Button>,
                <Button key="save" onClick={handleSaveInjectConfig}>保存配置</Button>,
                <Button key="inject" type="primary" onClick={handleInjectToRagflow} loading={injecting}
                    className="bg-orange-500 hover:bg-orange-600 border-none"
                >开始注入</Button>,
            ]}
        >
            <div className="bg-orange-50 p-3 mb-4 rounded border border-orange-100 flex gap-2">
                <InfoCircleOutlined className="text-orange-600 mt-0.5 flex-shrink-0" />
                <div className="text-orange-800 text-sm">将当前本体图谱注入到 RAGFlow 知识库中，使其支持知识图谱检索。请先确保 RAGFlow 服务已启动且知识库已创建。</div>
            </div>
            <Form form={injectForm} layout="vertical">
                <div className="grid grid-cols-1 gap-3">
                    <Form.Item name="ragflow_host" label="RAGFlow 地址" rules={[{ required: true, message: '请输入RAGFlow地址' }]}>
                        <Input placeholder="http://localhost:9380" />
                    </Form.Item>
                    <Form.Item name="ragflow_api_key" label="RAGFlow API Key" rules={[{ required: true, message: '请输入RAGFlow API Key' }]}>
                        <Input.Password placeholder="ragflow-xxxxxxxxxxxx" />
                    </Form.Item>
                    <div className="mb-1">
                        <Button size="small" onClick={handleFetchRagflowInfo} loading={fetchingRagflow}
                            className="bg-blue-500 hover:bg-blue-600 text-white border-none"
                        >
                            获取RAGFlow信息
                        </Button>
                        <span className="text-gray-400 text-xs ml-2">填写地址和API Key后点击，自动获取知识库列表</span>
                    </div>
                    <Form.Item name="kb_id" label="知识库" rules={[{ required: true, message: '请选择知识库' }]}>
                        <Select placeholder="点击上方按钮获取知识库列表" showSearch optionFilterProp="label"
                            notFoundContent={ragflowDatasets.length === 0 ? '请先获取RAGFlow信息' : '无知识库'}
                        >
                            {ragflowDatasets.map(ds => (
                                <Select.Option key={ds.id} value={ds.id} label={ds.name}>
                                    <div className="flex justify-between">
                                        <span>{ds.name}</span>
                                        <span className="text-gray-400 text-xs">{ds.id}</span>
                                    </div>
                                </Select.Option>
                            ))}
                        </Select>
                    </Form.Item>
                </div>
            </Form>
        </Modal>
    );
};

export default RagSyncModal;

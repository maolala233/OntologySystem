/**
 * 知识域选择器组件
 * 支持下拉选择已有知识域，也支持输入新名称及描述并创建
 */
import React, { useState, useEffect } from 'react';
import { Select, Input, Button, Space, Modal, Form, message, Tag } from 'antd';
import { PlusOutlined, EditOutlined, DatabaseOutlined } from '@ant-design/icons';
import { getDomains, createDomain, KnowledgeDomain } from '../api/domains';

const { Option } = Select;
const { TextArea } = Input;

interface KnowledgeDomainSelectorProps {
    value?: number; // 选中的知识域 ID
    onChange?: (value: number | undefined) => void;
    domainName?: string; // 选中的知识域名称（用于新建项目时传递）
    onDomainNameChange?: (name: string | undefined) => void;
    placeholder?: string;
    allowCreate?: boolean;
    disabled?: boolean;
}

const KnowledgeDomainSelector: React.FC<KnowledgeDomainSelectorProps> = ({
    value,
    onChange,
    domainName,
    onDomainNameChange,
    placeholder = '选择知识域',
    allowCreate = true,
    disabled = false,
}) => {
    const [domains, setDomains] = useState<KnowledgeDomain[]>([]);
    const [loading, setLoading] = useState(false);
    const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
    const [createDomainForm] = Form.useForm();
    const [searchValue, setSearchValue] = useState('');

    // 加载知识域列表
    useEffect(() => {
        loadDomains();
    }, []);

    const loadDomains = async () => {
        setLoading(true);
        try {
            const data = await getDomains();
            setDomains(data);
        } catch (error: any) {
            message.error('加载知识域列表失败');
        } finally {
            setLoading(false);
        }
    };

    const handleCreateDomain = async () => {
        try {
            const values = await createDomainForm.validateFields();
            const newDomain = await createDomain({
                name: values.name,
                description: values.description,
            });
            
            message.success(`知识域 "${values.name}" 创建成功`);
            
            // 刷新列表
            await loadDomains();
            
            // 选中新创建的知识域
            onChange?.(newDomain.id);
            onDomainNameChange?.(newDomain.name);
            
            // 关闭弹窗
            setIsCreateModalOpen(false);
            createDomainForm.resetFields();
        } catch (error: any) {
            if (error.response?.status === 400) {
                message.warning('该知识域名称已存在');
            } else {
                message.error('创建知识域失败');
            }
        }
    };

    const handleSelectChange = (selectedValue: number | string) => {
        const selectedId = typeof selectedValue === 'string' ? parseInt(selectedValue) : selectedValue;
        const selectedDomain = domains.find(d => d.id === selectedId);
        
        onChange?.(selectedId);
        onDomainNameChange?.(selectedDomain?.name);
    };

    const handleClear = () => {
        onChange?.(undefined);
        onDomainNameChange?.(undefined);
        setSearchValue('');
    };

    // 过滤选项
    const filteredOptions = domains.filter(domain => 
        !searchValue || 
        domain.name.toLowerCase().includes(searchValue.toLowerCase()) ||
        domain.description?.toLowerCase().includes(searchValue.toLowerCase())
    );

    // 获取选中的知识域名称
    const selectedDomain = domains.find(d => d.id === value);

    return (
        <>
            <div className="flex items-center gap-2">
                <div className="flex-1">
                    <Select
                        value={value}
                        onChange={handleSelectChange}
                        onClear={handleClear}
                        placeholder={placeholder}
                        loading={loading}
                        disabled={disabled}
                        showSearch
                        filterOption={false}
                        onSearch={setSearchValue}
                        labelRender={() => selectedDomain?.name || placeholder}
                        dropdownRender={(menu) => (
                            <>
                                <div className="max-h-64 overflow-y-auto">
                                    {filteredOptions.map((domain) => (
                                        <div
                                            key={domain.id}
                                            className={`px-3 py-2 cursor-pointer hover:bg-gray-100 ${domain.id === value ? 'bg-blue-50' : ''}`}
                                            onClick={() => handleSelectChange(domain.id)}
                                        >
                                            <div className="flex items-center justify-between">
                                                <span className="font-medium text-gray-900">{domain.name}</span>
                                                {domain.id === value && <Tag color="blue" className="text-xs">已选择</Tag>}
                                            </div>
                                            {domain.description && (
                                                <span className="text-xs text-gray-500 block mt-1">
                                                    {domain.description}
                                                </span>
                                            )}
                                        </div>
                                    ))}
                                </div>
                                {allowCreate && (
                                    <div style={{ padding: '8px 12px', borderTop: '1px solid #f0f0f0' }}>
                                        <Button
                                            type="dashed"
                                            block
                                            icon={<PlusOutlined />}
                                            onClick={() => setIsCreateModalOpen(true)}
                                        >
                                            新建知识域
                                        </Button>
                                    </div>
                                )}
                            </>
                        )}
                        options={[]}
                        style={{ width: '100%' }}
                    />
                </div>
            </div>

            {/* 新建知识域弹窗 */}
            <Modal
                title={
                    <div className="flex items-center gap-2">
                        <DatabaseOutlined className="text-indigo-600" />
                        <span>新建知识域</span>
                    </div>
                }
                open={isCreateModalOpen}
                onCancel={() => {
                    setIsCreateModalOpen(false);
                    createDomainForm.resetFields();
                }}
                onOk={handleCreateDomain}
                okText="创建"
                cancelText="取消"
                width={450}
            >
                <div className="py-4">
                    <div className="bg-blue-50 border border-blue-100 rounded-lg p-3 mb-4 text-sm text-gray-700">
                        <p className="font-medium text-blue-800 mb-1">什么是「知识域」？</p>
                        <p>
                            知识域就像图书馆里的「书架分类」：告诉系统这个项目属于哪个业务领域（如 IT 架构、医疗健康）。
                            它决定了 ① 项目的归属分类，② 发布后在资产中心里按领域被检索到。
                            填不确定也没关系，先选一个最接近的，后续可随时修改。
                        </p>
                    </div>
                    <p className="text-gray-500 text-sm mb-4">
                        没有合适的分类？直接在下方输入新名称即可创建。
                    </p>
                    <Form form={createDomainForm} layout="vertical">
                        <Form.Item
                            name="name"
                            label="知识域名称"
                            rules={[
                                { required: true, message: '请输入知识域名称' },
                                { max: 50, message: '名称不能超过 50 个字符' },
                            ]}
                        >
                            <Input placeholder="例如：IT 架构、财务规范、医疗健康" />
                        </Form.Item>
                        <Form.Item
                            name="description"
                            label="知识域描述"
                            rules={[{ max: 200, message: '描述不能超过 200 个字符' }]}
                        >
                            <TextArea
                                rows={3}
                                placeholder="简要描述该知识域的适用范围..."
                            />
                        </Form.Item>
                    </Form>
                </div>
            </Modal>
        </>
    );
};

export default KnowledgeDomainSelector;
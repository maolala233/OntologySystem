/**
 * 知识域选择器组件
 * 支持下拉选择已有知识域，也支持输入新名称及描述并创建
 */
import React, { useState, useEffect } from 'react';
import { message, notifyError } from '../shared/feedback';
import { Select, Input, Button, Space, Modal, Form, Tag } from 'antd';
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
    // 受控下拉开合：选中/新建后自动收起——用户"选了即填入"，不必再点其他区域
    const [open, setOpen] = useState(false);

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
            notifyError(error, '加载知识域列表失败');
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
            
            message.success(`知识域「${values.name}」已创建`);
            
            // 刷新列表
            await loadDomains();
            
            // 选中新创建的知识域
            onChange?.(newDomain.id);
            onDomainNameChange?.(newDomain.name);
            setOpen(false);

            // 关闭弹窗
            setIsCreateModalOpen(false);
            createDomainForm.resetFields();
        } catch (error: any) {
            // 409：后端重名拦截（400 为历史口径，一并兼容）
            if (error.response?.status === 400 || error.response?.status === 409) {
                message.warning('该知识域名称已存在，请直接从下拉列表选择');
            } else {
                notifyError(error, '创建知识域失败');
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
        setOpen(false);
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
            <div className="w-full">
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
                    open={open}
                    onDropdownVisibleChange={setOpen}
                    labelRender={() => selectedDomain?.name
                        || <span className="text-[#A9B5CC]">{placeholder}</span>}
                    dropdownRender={() => (
                        <>
                            <div className="max-h-64 overflow-y-auto">
                                {filteredOptions.map((domain) => (
                                    <div
                                        key={domain.id}
                                        className={`cursor-pointer px-3 py-2 transition-colors hover:bg-[rgba(91,141,239,0.08)] ${
                                            domain.id === value ? 'bg-[rgba(91,141,239,0.12)]' : ''}`}
                                        // mousedown preventDefault：不拦则 mousedown 先让
                                        // Select 失焦收起下拉，click 永远完不成——
                                        // 选项"点了没反应"的根因（2026-11-08 实测）
                                        onMouseDown={(e) => e.preventDefault()}
                                        onClick={() => {
                                            handleSelectChange(domain.id);
                                            setOpen(false); // 选中即收起，输入框即时回填
                                        }}
                                    >
                                        <div className="flex items-center justify-between gap-2">
                                            <span className="font-medium text-[#1F2A44]">{domain.name}</span>
                                            {domain.id === value && <Tag color="blue" className="text-xs">已选择</Tag>}
                                        </div>
                                        {domain.description && (
                                            <span className="mt-1 block text-xs leading-5 text-[#8A94AB]">
                                                {domain.description}
                                            </span>
                                        )}
                                    </div>
                                ))}
                            </div>
                            {allowCreate && (
                                <div style={{ padding: '8px 12px', borderTop: '1px solid #E4ECF8' }}>
                                    <Button
                                        block
                                        icon={<PlusOutlined />}
                                        className="tk-soft-btn tk-soft-btn-dashed"
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

            {/* 新建知识域弹窗 */}
            <Modal
                title={
                    <div className="flex items-center gap-2">
                        <DatabaseOutlined className="text-[#5B8DEF]" />
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
                <div className="pt-0.5">
                    <div className="mb-3 rounded-[10px] border border-[rgba(91,141,239,0.18)] bg-[rgba(91,141,239,0.07)] px-3 py-2.5 text-[12.5px] leading-5 text-[#52607A]">
                        <p className="mb-1 font-semibold text-[#2A57C6]">什么是「知识域」？</p>
                        <p>
                            知识域就像图书馆里的「书架分类」：告诉系统这个项目属于哪个业务领域（如 IT 架构、医疗健康）。
                            它决定了 ① 项目的归属分类，② 发布后在资产中心里按领域被检索到。
                            填不确定也没关系，先选一个最接近的，后续可随时修改。
                        </p>
                    </div>
                    <p className="mb-3 text-[12.5px] leading-5 text-[#8A94AB]">
                        没有合适的分类？直接在下方输入新名称即可创建。
                    </p>
                    <Form form={createDomainForm} layout="vertical">
                        <Form.Item
                            name="name"
                            label="知识域名称"
                            style={{ marginBottom: 14 }}
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
                            style={{ marginBottom: 0 }}
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
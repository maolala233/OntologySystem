/**
 * 发布流程（2026-11-07）：知识域选择从构建期移到发布期。
 * 点发布直接发；项目无知识域被后端 DOMAIN_REQUIRED 拦下时弹选择器
 * （可选已有或输入新名称创建），确认后带域重发——选域+发布一步完成。
 * 已设域的项目发布体验不变（一次点击直发）。
 */
import React from 'react';
import { message, confirmModal } from '../../../shared/feedback';


import KnowledgeDomainSelector from '../../../components/KnowledgeDomainSelector';
import { publishApi } from '../../../api/governance';

export interface PublishResult {
    version_no: number;
    warnings?: any[];
}

/** 弹窗内联选择面板：内部持有选中态并接回 value——不接的话 onChange 虽触发、
 * 输入框永远显示占位符（"选了没反应"的另一半根因），选中后即时回填 */
const DomainPickPane: React.FC<{ onPick: (id?: number, name?: string) => void }> = ({ onPick }) => {
    const [id, setId] = React.useState<number | undefined>(undefined);
    const [name, setName] = React.useState<string | undefined>(undefined);
    return (
        <KnowledgeDomainSelector
            value={id}
            onChange={(v) => { setId(v); onPick(v, name); }}
            domainName={name}
            onDomainNameChange={(n) => { setName(n); onPick(id, n); }}
        />
    );
};

/**
 * 尝试发布；无知识域时弹选择器后带域重发。
 * 返回发布结果；用户取消/发布失败返回 null；其他错误（门控未过等）原样抛出，
 * 由调用方的既有错误处理展示。
 */
export async function publishWithDomain(projectId: number): Promise<PublishResult | null> {
    try {
        return await publishApi.publish(projectId, {});
    } catch (e: any) {
        const code = e?.response?.data?.error?.code;
        if (code !== 'DOMAIN_REQUIRED') throw e;
    }

    let domainId: number | undefined;
    let domainName: string | undefined;
    return new Promise((resolve) => {
        confirmModal({
            title: '选择知识域后发布',
            content: (
                <div className="py-2">
                    <p className="text-gray-500 text-xs mb-3">
                        发布前需要给项目指定知识域，资产中心按知识域分组展示。
                    </p>
                    <DomainPickPane onPick={(id, name) => { domainId = id; domainName = name; }} />
                </div>
            ),
            okText: '发布', cancelText: '取消',
            onOk: async () => {
                if (!domainId && !domainName) {
                    message.warning('请选择已有知识域，或输入新名称创建');
                    return Promise.reject(); // 阻止弹窗关闭
                }
                try {
                    resolve(await publishApi.publish(projectId, {
                        domain_id: domainId, domain_name: domainName,
                    }));
                } catch (e: any) {
                    const err = e?.response?.data?.error || e?.response?.data;
                    message.error(err?.message || '发布失败');
                    resolve(null);
                }
            },
            onCancel: () => resolve(null),
        });
    });
}

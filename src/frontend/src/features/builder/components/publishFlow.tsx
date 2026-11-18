/**
 * 发布流程（2026-11-07）：知识域选择从构建期移到发布期。
 * 点发布直接发；项目无知识域被后端 DOMAIN_REQUIRED 拦下时弹选择器
 * （可选已有或输入新名称创建），确认后带域重发——选域+发布一步完成。
 * 已设域的项目发布体验不变（一次点击直发）。
 *
 * 2026-11-19：
 *  - 调用方已知项目没有知识域时（hasDomain === false）直接弹选择器，
 *    不再先发一个注定 400 的探测请求；
 *  - 选择面板改用合并状态：antd 在同一 tick 内先后回调 onChange /
 *    onDomainNameChange，旧写法会用闭包里的另一份旧值回写，
 *    domain_id 永远被覆盖成 undefined（只剩名字），带域重发因此可能一直 400；
 *  - 弹窗内发布失败不自动关闭，提示后端信息后可直接重试。
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
    // 选中一个域时，KnowledgeDomainSelector 会在同一 tick 内先后回调
    // onChange(id) 与 onDomainNameChange(name)；用 ref 合并两份值，
    // 避免后一次回调拿旧闭包把前一次的结果覆盖成 undefined
    const pickedRef = React.useRef<{ id?: number; name?: string }>({});
    const [picked, setPicked] = React.useState<{ id?: number; name?: string }>({});
    const emit = (patch: { id?: number; name?: string }) => {
        const next = { ...pickedRef.current, ...patch };
        pickedRef.current = next;
        setPicked(next);
        onPick(next.id, next.name);
    };
    return (
        <KnowledgeDomainSelector
            value={picked.id}
            onChange={(v) => emit({ id: v })}
            domainName={picked.name}
            onDomainNameChange={(n) => emit({ name: n })}
        />
    );
};

/**
 * 尝试发布；无知识域时弹选择器后带域重发。
 * 返回发布结果；用户取消/发布失败返回 null；其他错误（门控未过等）原样抛出，
 * 由调用方的既有错误处理展示。
 */
export async function publishWithDomain(
    projectId: number,
    opts?: { hasDomain?: boolean },
): Promise<PublishResult | null> {
    // hasDomain === false：调用方已经知道项目没有知识域，别再发一次注定 400 的探测
    if (opts?.hasDomain !== false) {
        try {
            return await publishApi.publish(projectId, {});
        } catch (e: any) {
            const code = e?.response?.data?.error?.code;
            if (code !== 'DOMAIN_REQUIRED') throw e;
        }
    }

    let domainId: number | undefined;
    let domainName: string | undefined;
    return new Promise((resolve) => {
        confirmModal({
            title: '选择知识域后发布',
            // 这是“选一个值再发布”的表单弹窗，不是告警确认：去掉默认感叹号图标，
            // 否则正文会被 icon 缩进约 38px，下拉框和标题、按钮对不齐，窄弹窗里特别明显
            icon: null,
            width: 480,
            centered: true,
            // 表单型确认框：标题也带那枚彩条（纯警示确认不加）
            className: 'tk-confirm-mark',
            content: (
                <div className="pt-1">
                    <p className="mb-2.5 text-[12.5px] leading-5 text-[#8A94AB]">
                        发布前需要给项目指定知识域，资产中心按知识域分组展示。
                    </p>
                    <span className="tk-field-label">知识域</span>
                    <DomainPickPane onPick={(id, name) => { domainId = id; domainName = name; }} />
                    <p className="mt-2 text-[11.5px] leading-5 text-[#A3ADC2]">
                        没有合适的分类？在下拉里点「新建知识域」即可现场创建。
                    </p>
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
                    return Promise.reject(); // 保持弹窗打开：改选/重试即可，不必回页面再点一次
                }
            },
            onCancel: () => resolve(null),
        });
    });
}

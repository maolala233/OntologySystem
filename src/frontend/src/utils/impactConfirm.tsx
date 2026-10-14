/**
 * F10 破坏性确认统一模板（09 §2）：确认弹窗必须先讲清「会失去什么」——
 * 影响范围说明逐条列出连带后果，参照审核台 humanSummary 的人话风格。
 * 用法：confirmImpact({ title, impact: ['…', '…'], onOk });
 */
import React from 'react';
import { Modal } from 'antd';

export interface ImpactConfirmOptions {
    title: string;
    /** 影响范围说明：每条一行，描述该操作会连带失去/改变什么 */
    impact: string[];
    okText?: string;
    danger?: boolean;
    /** 追加在影响范围之后的自定义内容（如输入确认编号） */
    extra?: React.ReactNode;
    onOk: () => void | Promise<any>;
    onCancel?: () => void;
}

const confirmImpact = ({
    title, impact, okText = '确定', danger = true, extra, onOk, onCancel,
}: ImpactConfirmOptions) => {
    Modal.confirm({
        title,
        width: 460,
        content: (
            <div>
                <div className="text-sm font-medium mt-2 mb-1">影响范围说明</div>
                <ul className="list-disc pl-5 space-y-1 text-sm text-gray-700">
                    {impact.map((line, i) => <li key={i}>{line}</li>)}
                </ul>
                {extra && <div className="mt-3">{extra}</div>}
            </div>
        ),
        okText,
        okButtonProps: { danger },
        cancelText: '取消',
        onOk,
        onCancel,
    });
};

export default confirmImpact;

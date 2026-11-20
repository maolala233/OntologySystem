/**
 * antd 反馈桥（2026-11-08）：antd 的 message/Modal 静态方法不能消费 App 上下文，
 * dev console 持续告警 "Static function can not consume context like dynamic
 * theme. Please use 'App' component instead"（PipelinePanel.tsx:199 等静态调用点）。
 *
 * FeedbackBridge 挂在根 <AntdApp> 内，把 App.useApp() 实例写入模块级 holder；
 * 业务代码统一 `import { message, confirmModal, notifyError } from '<rel>/shared/feedback'`，
 * 调用点写法不变（utils 等非组件模块也能用，这是 useApp hook 做不到的）。
 * 2026-11-20 起错误提示统一走 notifyError()：拦截器不再自己弹 403，避免一处失败弹两条。
 * 桥挂载前（极端早期调用）回落 antd 静态方法。
 */
import { useEffect } from 'react';
import { App as AntdApp, message as staticMessage, Modal as staticModal } from 'antd';
import type { MessageInstance } from 'antd/es/message/interface';
import type { HookAPI as ModalHookAPI } from 'antd/es/modal/useModal';

const holder: { message?: MessageInstance; modal?: ModalHookAPI } = {};

/** 挂载于根 <AntdApp> 内：注入可消费主题上下文的 message/modal 实例 */
export function FeedbackBridge(): React.ReactElement | null {
    const { message: m, modal: md } = AntdApp.useApp();
    useEffect(() => {
        holder.message = m;
        holder.modal = md;
    }, [m, md]);
    return null;
}

export const message = {
    success: ((...a) => (holder.message ?? staticMessage).success(...a)) as MessageInstance['success'],
    error: ((...a) => (holder.message ?? staticMessage).error(...a)) as MessageInstance['error'],
    warning: ((...a) => (holder.message ?? staticMessage).warning(...a)) as MessageInstance['warning'],
    info: ((...a) => (holder.message ?? staticMessage).info(...a)) as MessageInstance['info'],
    loading: ((...a) => (holder.message ?? staticMessage).loading(...a)) as MessageInstance['loading'],
    open: ((...a) => (holder.message ?? staticMessage).open(...a)) as MessageInstance['open'],
};

/** 替代 Modal.confirm 等静态调用（消费主题上下文） */
export const confirmModal: ModalHookAPI['confirm'] = ((...a) =>
    (holder.modal ?? staticModal).confirm(...a)) as ModalHookAPI['confirm'];
export const warningModal: ModalHookAPI['warning'] = ((...a) =>
    (holder.modal ?? staticModal).warning(...a)) as ModalHookAPI['warning'];
export const infoModal: ModalHookAPI['info'] = ((...a) =>
    (holder.modal ?? staticModal).info(...a)) as ModalHookAPI['info'];
export const errorModal: ModalHookAPI['error'] = ((...a) =>
    (holder.modal ?? staticModal).error(...a)) as ModalHookAPI['error'];

/**
 * 统一错误文案（2026-11-20）：
 * 各页 catch 里统一用 notifyError(e, '保存失败')，由这里挑一条最具体的文案，
 * 避免「响应拦截器弹一条 + 调用方 catch 再弹一条」的重复提示。
 * 优先级：403 错误码的行动文案 → 统一包络 error.message → detail → data.message →
 * 无响应体时异常自身文案（本地校验 / MCP-RPC / 断网超时）→ 兜底文案。
 */
const ERROR_CODE_TEXT: Record<string, string> = {
    // 后端 message 偏工程化（如「未开通模块: documents」），换成用户能照着做的文案
    MODULE_NOT_GRANTED: '未开通该功能模块，请联系管理员授权',
    ADMIN_REQUIRED: '该操作需要管理员权限',
    PUBLISHED_READONLY: '公共资产为只读快照，如需修改请取消发布或另起新版本',
    PIPELINE_RUNNING: '构建进行中，结束后才能修改文档',
};

export function errorText(err: any, fallback = '操作失败'): string {
    const code: string | undefined = err?.response?.data?.error?.code;
    if (code && ERROR_CODE_TEXT[code]) return ERROR_CODE_TEXT[code];

    const envelope = err?.response?.data?.error?.message;
    if (typeof envelope === 'string' && envelope.trim()) return envelope;

    const detail = err?.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (detail && typeof detail === 'object'
        && typeof detail.message === 'string' && detail.message.trim()) {
        return detail.message;
    }

    const dataMessage = err?.response?.data?.message;
    if (typeof dataMessage === 'string' && dataMessage.trim()) return dataMessage;

    if (!err?.response) {
        if (err?.code === 'ECONNABORTED' || /timeout/i.test(String(err?.message || ''))) {
            return '请求超时，请稍后重试';
        }
        if (err?.message === 'Network Error' || err?.message === 'Failed to fetch') {
            return '网络连接失败，请检查网络';
        }
        if (typeof err?.message === 'string' && err.message.trim()) return err.message;
    }
    return fallback;
}

/** 业务侧统一错误提示入口：接口失败只弹这一条 */
export function notifyError(err: any, fallback = '操作失败'): void {
    // 只读快照属于「操作被规则挡住」，不是系统错误 → 保持橙色警告，与改版前的拦截器一致
    if (err?.response?.data?.error?.code === 'PUBLISHED_READONLY') {
        message.warning(errorText(err, fallback));
        return;
    }
    message.error(errorText(err, fallback));
}

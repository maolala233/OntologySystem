/**
 * antd 反馈桥（2026-11-08）：antd 的 message/Modal 静态方法不能消费 App 上下文，
 * dev console 持续告警 "Static function can not consume context like dynamic
 * theme. Please use 'App' component instead"（PipelinePanel.tsx:199 等静态调用点）。
 *
 * FeedbackBridge 挂在根 <AntdApp> 内，把 App.useApp() 实例写入模块级 holder；
 * 业务代码统一 `import { message, confirmModal } from '<rel>/shared/feedback'`，
 * 调用点写法不变（utils 等非组件模块也能用，这是 useApp hook 做不到的）。
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

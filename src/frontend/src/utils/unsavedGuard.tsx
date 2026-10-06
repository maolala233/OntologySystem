/**
 * 未保存修改守卫：画布类页面（骨架编辑 / 实例探索）登记脏状态，
 * 任何离开动作（构建器 Tab 切换、返回、侧边栏导航、浏览器关闭/刷新）
 * 前统一弹窗确认。离开确认后不自动保存——未保存即维持上次落库的版本。
 */
import React from 'react';
import { Modal } from 'antd';
import { ExclamationCircleFilled } from '@ant-design/icons';

const registry = new Set<string>();

export const setUnsavedGuard = (key: string, active: boolean) => {
    if (active) registry.add(key);
    else registry.delete(key);
};

export const hasUnsavedChanges = () => registry.size > 0;

/** 有未保存修改时弹确认；无修改直接放行 */
export const confirmLeaveUnsaved = (onOk: () => void) => {
    if (!hasUnsavedChanges()) {
        onOk();
        return;
    }
    Modal.confirm({
        title: '有未保存的修改',
        icon: React.createElement(ExclamationCircleFilled),
        content: '当前画布有未保存的修改，离开后将丢失（数据库仍保持上次保存的版本）。确定要离开吗？',
        okText: '放弃修改并离开',
        okType: 'danger',
        cancelText: '继续编辑',
        onOk,
    });
};

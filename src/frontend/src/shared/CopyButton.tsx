/**
 * 全站统一的复制按钮（2026-11-25）。
 *
 * 交互：点击 → 图标在原位换成 ✓（1.5s 后复原）。
 * - 不挂悬停气泡：复制图标本身已经说明动作，「复制 XX」气泡只是重复；
 * - 不用浮层文字提示「已复制」：结果短的时候浮层会压住图标/内容，原位对钩更干净；
 * - 只有复制失败（剪贴板权限被拒等）才弹一次站内提醒。
 *
 * 用法：
 *   <CopyButton text={plainToken} size="small" type="text" />
 *   <CopyButton text={() => JSON.stringify(payload, null, 2)} className="tk-soft-btn">复制参数</CopyButton>
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import type { ButtonProps } from 'antd';
import { CheckCircleFilled, CopyOutlined } from '@ant-design/icons';
import { message } from './feedback';

export interface CopyButtonProps extends Omit<ButtonProps, 'icon' | 'onClick'> {
    /** 要复制的内容；传函数可以延迟取值（点击时才计算） */
    text: string | (() => string);
    /** 复制失败时的提示文案 */
    failText?: string;
    /** 成功对钩的颜色类：浅色底默认青绿，深色底可覆盖 */
    checkClassName?: string;
}

const CopyButton: React.FC<CopyButtonProps> = ({
    text,
    failText = '复制失败，请手动复制',
    checkClassName = 'text-[#1B87AD]',
    'aria-label': ariaLabel = '复制',
    ...rest
}) => {
    const [copied, setCopied] = useState(false);
    const timerRef = useRef<number | null>(null);

    // 卸载时清掉未触发的复原定时器
    useEffect(() => () => {
        if (timerRef.current != null) window.clearTimeout(timerRef.current);
    }, []);

    const copy = useCallback(async () => {
        try {
            await navigator.clipboard.writeText(typeof text === 'function' ? text() : text);
            setCopied(true);
            if (timerRef.current != null) window.clearTimeout(timerRef.current);
            timerRef.current = window.setTimeout(() => setCopied(false), 1500);
        } catch {
            message.warning(failText);
        }
    }, [text, failText]);

    return (
        <Button
            {...rest}
            aria-label={ariaLabel}
            icon={copied ? <CheckCircleFilled className={checkClassName} /> : <CopyOutlined />}
            onClick={copy}
        />
    );
};

export default CopyButton;

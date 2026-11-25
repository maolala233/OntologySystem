/**
 * 搜索输入的统一收口（2026-11-25）。
 *
 * 站内搜索分两类：服务端按关键词查询（我的项目）与本地过滤（资产中心、类/实例列表树、
 * MCP 工具列表、授权弹窗的用户列表）。两类都把 onChange 的即时值直接交给业务，会有两个问题：
 *  1) 中文/日文输入法在拼音组字阶段就会触发 onChange（值是一串拼音字母），
 *     用户还在选字，请求/过滤已经按 "xiangmu" 跑过一遍；
 *  2) 连续输入时每个字符都触发一次过滤——列表树要重爬整张图，主线程被反复打断。
 *
 * 这里把「显示值」和「查询值」拆开：
 *  - value 即时回显，输入框不会丢字；
 *  - 组字期间只改 value，不提交查询；
 *  - 组字结束或普通输入停顿 delay 毫秒后，query 才更新；清空立即生效。
 *
 * 用法：把 value / setValue / inputProps 接到输入框，业务只读 query。
 *
 *   const { value, setValue, query, inputProps } = useImeSafeSearch(300);
 *   <Input value={value} onChange={e => setValue(e.target.value)} {...inputProps} />
 *   const list = useMemo(() => filterBy(query), [query]);
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { CompositionEvent } from 'react';

export interface ImeSafeSearchInputProps {
    onCompositionStart: () => void;
    onCompositionEnd: (e: CompositionEvent<HTMLInputElement>) => void;
}

export interface ImeSafeSearch {
    /** 输入框显示值：即时更新，包含拼音中间态 */
    value: string;
    /** 输入框 onChange 用 */
    setValue: (next: string) => void;
    /** 防抖 + 组字结束后的查询值：业务只读它 */
    query: string;
    /** 透传给输入框的组合事件 */
    inputProps: ImeSafeSearchInputProps;
    /** 清空（立即生效，不等防抖） */
    reset: () => void;
}

export function useImeSafeSearch(
    delay = 250,
    initialValue = '',
    /**
     * 查询值真正生效时的回调（与 setQuery 同一次渲染批次）。
     * 「我的项目」用它把页码一并重置，翻页态下输入搜索词只发一次请求：
     * 若放到 onChange 或单独 effect 里，会先按旧关键词拉一遍新页码。
     */
    onCommit?: (query: string) => void,
): ImeSafeSearch {
    const [value, setValueState] = useState(initialValue);
    const [query, setQuery] = useState(initialValue.trim());
    const composingRef = useRef(false);
    const timerRef = useRef<number | null>(null);

    const clearTimer = useCallback(() => {
        if (timerRef.current !== null) {
            window.clearTimeout(timerRef.current);
            timerRef.current = null;
        }
    }, []);

    // 空串不等防抖：点「清除」后列表要立刻回来，否则会有一下残留的旧结果
    const commit = useCallback((next: string, wait: number) => {
        clearTimer();
        const text = next.trim();
        if (!text) {
            setQuery('');
            onCommit?.('');
            return;
        }
        timerRef.current = window.setTimeout(() => {
            timerRef.current = null;
            setQuery(text);
            onCommit?.(text);
        }, wait);
    }, [clearTimer, onCommit]);

    const setValue = useCallback((next: string) => {
        setValueState(next);
        // 组字中只回显：拼音中间态不提交，等 compositionend 时统一补一次
        if (!composingRef.current) commit(next, delay);
    }, [commit, delay]);

    const onCompositionStart = useCallback(() => {
        composingRef.current = true;
        // 组字前挂着的定时器要掐掉，否则上一次的查询值会在拼音打到一半时生效
        clearTimer();
    }, [clearTimer]);

    const onCompositionEnd = useCallback((e: CompositionEvent<HTMLInputElement>) => {
        composingRef.current = false;
        const next = e.currentTarget.value;
        setValueState(next);
        commit(next, delay);
    }, [commit, delay]);

    const reset = useCallback(() => {
        setValueState('');
        clearTimer();
        setQuery('');
    }, [clearTimer]);

    // 卸载时清掉未触发的定时器
    useEffect(() => clearTimer, [clearTimer]);

    return {
        value,
        setValue,
        query,
        inputProps: { onCompositionStart, onCompositionEnd },
        reset,
    };
}

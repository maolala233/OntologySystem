/**
 * 去掉 antd 组件自动挂上的原生 title（2026-11-19 用户反馈）
 *
 * 这些白底提示都来自 rc-* 组件写进 DOM 的 title 属性，内容就是把可见文字再念一遍：
 * - 分页：rc-pagination 给页码、上一页/下一页、每页条数选择器都挂了 title；
 * - 树：rc-tree 给每个节点的内容容器挂了 title，内容就是节点名；
 * - 下拉：rc-select 给已选项、以及展开后的每个选项都挂了 title，内容就是选项文案。
 * 浏览器把它画在光标右下角（白底、约 1 秒延迟），和全站深色 Tooltip 是两套观感。
 * antd 没有统一开关（Pagination 的 showTitle 只覆盖一部分，ConfigProvider 也不透传），
 * 所以在鼠标进入时按需摘掉：只影响 hover，键盘/读屏仍能读到原有的 title。
 *
 * 范围：分页、树、下拉已选项与展开后的选项列表。其它 antd 原生 title
 * （如 Table ellipsis 单元格）不在这里动，需要时再扩。
 */
const STRIP_SCOPE = '.ant-pagination, .ant-tree, .ant-select-selection-item, .ant-select-dropdown';

/** 在应用启动时调用一次 */
export function installNativeTitleStripper() {
    document.addEventListener(
        'mouseover',
        (e) => {
            const target = e.target as HTMLElement | null;
            if (!target || typeof target.closest !== 'function') return;
            const withTitle = target.closest('[title]');
            if (withTitle && withTitle.closest(STRIP_SCOPE)) {
                withTitle.removeAttribute('title');
            }
        },
        true,
    );
}

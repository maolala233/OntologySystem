/**
 * shared/copilotBus - 画布选中态广播（O5-F12，docs/design/14 §5）
 * 构建器画布（SchemaTab 等）把选中节点/边写入此单例，全局悬浮 Copilot 小窗
 * 发送消息时读取——"针对我选中的东西说话"。模块级单例（非 React 状态）：
 * 选中态高频变动且只在小窗发送瞬间消费，无需触发渲染。
 */
import type { CopilotSelection } from '../api/copilot';

let current: CopilotSelection | null = null;

export function setCopilotSelection(sel: CopilotSelection | null): void {
    current = sel;
}

export function getCopilotSelection(): CopilotSelection | null {
    return current;
}

/** 从画布节点/边对象提取选中态（容错：字段缺失忽略） */
export function pickSelection(node: any, edge?: any): CopilotSelection {
    const out: CopilotSelection = { nodes: [], edges: [] };
    if (node?.id) {
        out.nodes.push({ id: String(node.id), label: String(node?.data?.label || node.id) });
    }
    if (edge?.id) {
        out.edges.push({
            id: String(edge.id),
            label: String(edge?.data?.label || edge?.data?.relation || edge.id),
        });
    }
    return out;
}

/**
 * 画布刷新广播（O5-F13）：提案在 Copilot 小窗应用成功后通知构建器刷新画布
 * （manual 版本已落库，重新拉取 graph_data 即可看到变更）。
 */
export const COPILOT_CANVAS_REFRESH = 'copilot:canvas-refresh';

export function notifyCanvasRefresh(): void {
    window.dispatchEvent(new CustomEvent(COPILOT_CANVAS_REFRESH));
}

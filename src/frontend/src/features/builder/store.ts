/**
 * 构建器共享项目数据（05 §4.3 zustand）：四 Tab 共享 project 元数据，
 * 画布 nodes/edges 仍由 SchemaTab 本地持有（两阶段交互逻辑不动，05 §7 禁止拆分同时改逻辑）。
 */
import { create } from 'zustand';

import { projectsApi } from '../../api/projects';

export type BuilderTab = 'documents' | 'schema' | 'graph' | 'timeline';

interface BuilderState {
    projectId: number | null;
    projectName: string;
    projectStatus: string;          // draft/building/ready/published（M3-6 状态机）
    isPublished: boolean;           // 兼容期双读
    domainName?: string;
    loading: boolean;
    loadError: boolean;
    loadProject: (projectId: number) => Promise<void>;
    reset: () => void;
}

export const useBuilderStore = create<BuilderState>((set) => ({
    projectId: null,
    projectName: '',
    projectStatus: 'draft',
    isPublished: false,
    domainName: undefined,
    loading: false,
    loadError: false,
    loadProject: async (projectId: number) => {
        // 切到别的项目时先清掉上一个项目的元数据：否则从 A 返回、再进 B 的瞬间，
        // 顶部标题/状态徽标/知识域会先显示 A 的内容，fetch 回来才被纠正（2026-11-12 实测）。
        // projectId 故意不在这里更新——它最后一次被赋值即"当前数据属于哪个项目"，
        // 消费者据 projectId === 路由 pid 判断是否已就绪，旧值恰好让判据为否
        set((state) => ({
            loading: true,
            loadError: false,
            ...(state.projectId === projectId ? {} : {
                projectName: '',
                projectStatus: 'draft',
                isPublished: false,
                domainName: undefined,
            }),
        }));
        try {
            const p = await projectsApi.getProject(projectId);
            set({
                projectId,
                projectName: p.name || '',
                // M3-6：写路径以 status 为准；后端未迁移完的老项目回退 is_published
                projectStatus: (p as any).status || (p.is_published ? 'published' : 'draft'),
                isPublished: p.is_published,
                domainName: p.domain?.name,
                loading: false,
            });
        } catch {
            set({ loading: false, loadError: true });
        }
    },
    reset: () => set({
        projectId: null, projectName: '', projectStatus: 'draft',
        isPublished: false, domainName: undefined, loadError: false,
    }),
}));

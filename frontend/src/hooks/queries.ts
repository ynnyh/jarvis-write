// hooks/queries.ts
// React Query hooks:替代手动 useState + reload() 模式。
// 所有项目级数据通过 query keys 管理缓存,mutation 后 invalidate 对应 key 即可刷新。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

// =============== Query Key Factory ===============
export const qk = {
  project: (pid: number) => ["project", pid] as const,
  architecture: (pid: number) => ["architecture", pid] as const,
  outlines: (pid: number) => ["outlines", pid] as const,
  chapters: (pid: number) => ["chapters", pid] as const,
  // 单章正文:写作/编辑部/润色共享同一缓存,章号来自 URL(见 useChapterContext)
  chapter: (pid: number, ch: number) => ["chapter", pid, ch] as const,
  cards: (pid: number) => ["cards", pid] as const,
  // 故事圣经人物(正文实体高亮/hover 卡复用;随一致性同步刷新)
  characters: (pid: number) => ["characters", pid] as const,
};

// =============== Data Hooks ===============

export function useProject(pid: number) {
  return useQuery({
    queryKey: qk.project(pid),
    queryFn: () => api.getProject(pid),
  });
}

export function useArchitecture(pid: number) {
  return useQuery({
    queryKey: qk.architecture(pid),
    queryFn: () => api.getArchitecture(pid).catch(() => null),
  });
}

export function useOutlines(pid: number) {
  return useQuery({
    queryKey: qk.outlines(pid),
    queryFn: () => api.listOutlines(pid),
  });
}

export function useChapters(pid: number) {
  return useQuery({
    queryKey: qk.chapters(pid),
    queryFn: () => api.listChapters(pid),
  });
}

/** 单章正文(共享缓存):ch 为 null 时不拉取。 */
export function useChapter(pid: number, ch: number | null) {
  return useQuery({
    queryKey: qk.chapter(pid, ch ?? 0),
    queryFn: () => api.getChapter(pid, ch!),
    enabled: ch !== null,
  });
}

// =============== 写作手法卡 ===============

export function useCards(pid: number) {
  return useQuery({
    queryKey: qk.cards(pid),
    queryFn: () => api.listCards(pid),
  });
}

/** 手法卡的增改删:成功后统一 invalidate ['cards', pid]。 */
export function useCardMutations(pid: number) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: qk.cards(pid) });

  const create = useMutation({
    mutationFn: (body: { title: string; body: string; enabled?: boolean }) =>
      api.createCard(pid, body),
    onSuccess: refresh,
  });
  const update = useMutation({
    mutationFn: (v: {
      id: number;
      patch: { title?: string; body?: string; enabled?: boolean; sort?: number };
    }) => api.updateCard(pid, v.id, v.patch),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteCard(pid, id),
    onSuccess: refresh,
  });
  return { create, update, remove };
}

// =============== 故事圣经人物(正文实体链接) ===============

/** 全书人物(名+别名+简介+关键事实),供正文实体高亮与 hover 卡使用。
 *  取数失败吞掉返空数组:实体链接是阅读增强,拉不到不该在正文区弹红字报错,退化为无高亮即可。 */
export function useCharacters(pid: number) {
  return useQuery({
    queryKey: qk.characters(pid),
    queryFn: () => api.characters(pid).then((r) => r.characters).catch(() => []),
  });
}

// =============== Invalidation Helper ===============

/** 返回一个函数,调用后刷新该项目的所有缓存数据(替代旧的 reload 回调)。 */
export function useInvalidateProject(pid: number) {
  const qc = useQueryClient();
  return async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["project", pid] }),
      qc.invalidateQueries({ queryKey: ["architecture", pid] }),
      qc.invalidateQueries({ queryKey: ["outlines", pid] }),
      qc.invalidateQueries({ queryKey: ["chapters", pid] }),
      qc.invalidateQueries({ queryKey: ["characters", pid] }),
    ]);
  };
}

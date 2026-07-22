// hooks/queries.ts
// React Query hooks:替代手动 useState + reload() 模式。
// 所有项目级数据通过 query keys 管理缓存,mutation 后 invalidate 对应 key 即可刷新。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

// =============== Query Key Factory ===============
export const qk = {
  project: (pid: number) => ["project", pid] as const,
  architecture: (pid: number) => ["architecture", pid] as const,
  outlines: (pid: number) => ["outlines", pid] as const,
  chapters: (pid: number) => ["chapters", pid] as const,
  // 全部项目级数据(用于一次性 invalidate)
  all: (pid: number) => ["project", pid] as const, // prefix match 会命中 project/architecture/outlines/chapters
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
    ]);
  };
}

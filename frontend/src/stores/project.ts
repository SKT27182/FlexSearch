import { create } from 'zustand';
import { projectsApi, type Project } from '@/lib/api';
import type { RagConfig, RagMode } from '@/lib/rag-types';

interface ProjectState {
  projects: Project[];
  currentProject: Project | null;
  isLoading: boolean;
  error: string | null;

  fetchProjects: () => Promise<void>;
  selectProject: (project: Project | null) => void;
  createProject: (
    name: string,
    description?: string,
    rag_config?: RagConfig,
    rag_mode?: RagMode
  ) => Promise<Project>;
  deleteProject: (id: string) => Promise<void>;
  reset: () => void;
}

/** Ignore stale list responses when multiple fetches overlap (e.g. React Strict Mode). */
let fetchGeneration = 0;

function sortProjects(projects: Project[]): Project[] {
  return [...projects].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );
}

function mergeProjectList(existing: Project[], incoming: Project[]): Project[] {
  const byId = new Map<string, Project>();
  for (const project of incoming) {
    byId.set(project.id, project);
  }
  for (const project of existing) {
    if (!byId.has(project.id)) {
      byId.set(project.id, project);
    }
  }
  return sortProjects([...byId.values()]);
}

export const useProjectStore = create<ProjectState>()((set, get) => ({
  projects: [],
  currentProject: null,
  isLoading: false,
  error: null,

  fetchProjects: async () => {
    const generation = ++fetchGeneration;
    const hadProjects = get().projects.length > 0;
    set({ isLoading: !hadProjects, error: null });
    try {
      const projects = await projectsApi.list();
      if (generation !== fetchGeneration) return;
      set({
        projects: sortProjects(projects),
        error: null,
      });
    } catch (err) {
      if (generation !== fetchGeneration) return;
      const message =
        err instanceof Error ? err.message : 'Failed to load projects';
      console.error('fetchProjects failed:', err);
      set({ error: message });
    } finally {
      if (generation === fetchGeneration) {
        set({ isLoading: false });
      }
    }
  },

  selectProject: (project) => {
    set({ currentProject: project });
  },

  createProject: async (name, description, rag_config, rag_mode = 'vector') => {
    const project = await projectsApi.create({
      name,
      description,
      rag_config,
      rag_mode,
    });
    set((state) => ({
      projects: mergeProjectList(state.projects, [project]),
      error: null,
    }));
    return project;
  },

  deleteProject: async (id) => {
    await projectsApi.delete(id);
    set((state) => ({
      projects: state.projects.filter((p) => p.id !== id),
      currentProject: state.currentProject?.id === id ? null : state.currentProject,
    }));
  },

  reset: () => {
    fetchGeneration += 1;
    set({
      projects: [],
      currentProject: null,
      isLoading: false,
      error: null,
    });
  },
}));

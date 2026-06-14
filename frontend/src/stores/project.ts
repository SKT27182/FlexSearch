import { create } from 'zustand';
import { projectsApi, type Project } from '@/lib/api';
import type { GraphRagConfig, RagMode, VectorRagConfig } from '@/lib/rag-types';

interface ProjectState {
  projects: Project[];
  currentProject: Project | null;
  isLoading: boolean;

  fetchProjects: () => Promise<void>;
  selectProject: (project: Project | null) => void;
  createProject: (
    name: string,
    description?: string,
    rag_mode?: RagMode,
    rag_config?: VectorRagConfig | GraphRagConfig
  ) => Promise<Project>;
  deleteProject: (id: string) => Promise<void>;
  reset: () => void;
}

export const useProjectStore = create<ProjectState>()((set) => ({
  projects: [],
  currentProject: null,
  isLoading: false,

  fetchProjects: async () => {
    set({ isLoading: true });
    try {
      const projects = await projectsApi.list();
      set({ projects });
    } finally {
      set({ isLoading: false });
    }
  },

  selectProject: (project) => {
    set({ currentProject: project });
  },

  createProject: async (name, description, rag_mode = 'vector', rag_config) => {
    const project = await projectsApi.create({
      name,
      description,
      rag_mode,
      rag_config,
    });
    set((state) => ({ projects: [project, ...state.projects] }));
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
    set({
      projects: [],
      currentProject: null,
      isLoading: false,
    });
  },
}));

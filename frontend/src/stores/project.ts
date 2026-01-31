import { create } from 'zustand';
import { projectsApi, sessionsApi, type Project, type Session } from '@/lib/api';

interface ProjectState {
  projects: Project[];
  currentProject: Project | null;
  sessions: Session[];
  currentSession: Session | null;
  isLoading: boolean;

  // Actions
  fetchProjects: () => Promise<void>;
  selectProject: (project: Project | null) => void;
  createProject: (name: string, description?: string) => Promise<Project>;
  deleteProject: (id: string) => Promise<void>;
  
  fetchSessions: (projectId: string) => Promise<void>;
  selectSession: (session: Session | null) => void;
  createSession: (name: string, projectId: string) => Promise<Session>;
  deleteSession: (id: string) => Promise<void>;
  
  // Reset all state
  reset: () => void;
}

export const useProjectStore = create<ProjectState>()((set, get) => ({
  projects: [],
  currentProject: null,
  sessions: [],
  currentSession: null,
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
    set({ currentProject: project, currentSession: null, sessions: [] });
    if (project) {
      get().fetchSessions(project.id);
    }
  },

  createProject: async (name, description) => {
    const project = await projectsApi.create({ name, description });
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

  fetchSessions: async (projectId) => {
    set({ isLoading: true });
    try {
      const sessions = await sessionsApi.list(projectId);
      set({ sessions });
    } finally {
      set({ isLoading: false });
    }
  },

  selectSession: (session) => {
    set({ currentSession: session });
  },

  createSession: async (name, projectId) => {
    const session = await sessionsApi.create({ name, project_id: projectId });
    set((state) => ({ sessions: [session, ...state.sessions] }));
    return session;
  },

  deleteSession: async (id) => {
    await sessionsApi.delete(id);
    set((state) => ({
      sessions: state.sessions.filter((s) => s.id !== id),
      currentSession: state.currentSession?.id === id ? null : state.currentSession,
    }));
  },

  reset: () => {
    set({
      projects: [],
      currentProject: null,
      sessions: [],
      currentSession: null,
      isLoading: false,
    });
  },
}));

import axios, { type AxiosError, type AxiosInstance } from 'axios';
import type { DocumentStatus, GraphIndexState, RagConfig, RagMode, RetrievalOverrides } from './rag-types';

const API_BASE_URL = '/api';

// Create axios instance
export const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor - add auth token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor - handle errors
api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    if (error.response?.status === 401) {
      // Token expired - try refresh or logout
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

// ============ Auth API ============

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface RegisterData {
  email: string;
  name: string;
  password: string;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

import type { UserRole } from './roles'

export interface User {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  created_at: string;
  updated_at: string;
}

export const authApi = {
  login: async (credentials: LoginCredentials): Promise<AuthTokens> => {
    const formData = new FormData();
    formData.append('username', credentials.email);
    formData.append('password', credentials.password);
    const { data } = await api.post<AuthTokens>('/auth/login', formData, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    return data;
  },

  register: async (data: RegisterData): Promise<User> => {
    const { data: user } = await api.post<User>('/auth/register', data);
    return user;
  },

  getMe: async (): Promise<User> => {
    const { data } = await api.get<User>('/auth/me');
    return data;
  },

  updateProfile: async (name: string): Promise<User> => {
    const { data } = await api.patch<User>('/auth/me/profile', { name });
    return data;
  },

  changePassword: async (
    currentPassword: string,
    newPassword: string
  ): Promise<void> => {
    await api.post('/auth/me/password', {
      current_password: currentPassword,
      new_password: newPassword,
    });
  },

  refresh: async (refreshToken: string): Promise<AuthTokens> => {
    const { data } = await api.post<AuthTokens>('/auth/refresh', {
      refresh_token: refreshToken,
    });
    return data;
  },
};

// ============ Projects API ============

export interface Project {
  id: string;
  name: string;
  description: string | null;
  owner_id: string;
  rag_mode: RagMode;
  rag_config: RagConfig;
  graph_index: GraphIndexState;
  document_count?: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectListResponse {
  projects: Project[];
  total: number;
}

export interface CreateProject {
  name: string;
  description?: string;
  rag_mode?: RagMode;
  rag_config?: RagConfig;
}

export const projectsApi = {
  list: async (): Promise<Project[]> => {
    const { data } = await api.get<ProjectListResponse>('/projects');
    return data.projects;
  },

  get: async (id: string): Promise<Project> => {
    const { data } = await api.get<Project>(`/projects/${id}`);
    return data;
  },

  create: async (project: CreateProject): Promise<Project> => {
    const { data } = await api.post<Project>('/projects', project);
    return data;
  },

  update: async (
    id: string,
    project: Partial<CreateProject> & { rag_config?: RagConfig }
  ): Promise<Project> => {
    const { data } = await api.patch<Project>(`/projects/${id}`, project);
    return data;
  },

  delete: async (id: string): Promise<void> => {
    await api.delete(`/projects/${id}`);
  },

  reindex: async (
    id: string,
    mode: 'auto' | 'full' | 'from_extracted' = 'auto'
  ): Promise<{ processed: number; message: string }> => {
    const { data } = await api.post<{ processed: number; message: string }>(
      `/projects/${id}/reindex`,
      { mode }
    );
    return data;
  },

  getGraphIndexStatus: async (id: string): Promise<GraphIndexState> => {
    const { data } = await api.get<GraphIndexState>(`/projects/${id}/graph-index/status`);
    return data;
  },

  rebuildGraphIndex: async (id: string): Promise<GraphIndexState> => {
    const { data } = await api.post<GraphIndexState>(`/projects/${id}/graph-index/rebuild`);
    return data;
  },

  downloadGraphExport: async (id: string): Promise<Blob> => {
    const { data } = await api.get<Blob>(`/projects/${id}/graph-export`, {
      responseType: 'blob',
    });
    return data;
  },

  switchRagMode: async (
    id: string,
    rag_mode: RagMode
  ): Promise<{ rag_mode: RagMode; message: string; documents_queued: number }> => {
    const { data } = await api.patch<{ rag_mode: RagMode; message: string; documents_queued: number }>(
      `/projects/${id}/rag-mode`,
      { rag_mode }
    );
    return data;
  },
};

// ============ Documents API ============

export interface Document {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: DocumentStatus;
  processing_step: string | null;
  progress_pct: number;
  error_message?: string | null;
  chunk_count: number;
  project_id: string;
  created_at: string;
  processed_at?: string | null;
}

export interface DocumentListResponse {
  documents: Document[];
  total: number;
}

export const documentsApi = {
  list: async (projectId: string): Promise<Document[]> => {
    const { data } = await api.get<DocumentListResponse>(`/projects/${projectId}/documents`);
    return data.documents;
  },

  upload: async (projectId: string, file: File): Promise<Document> => {
    const formData = new FormData();
    formData.append('file', file);
    const { data } = await api.post<Document>(`/projects/${projectId}/documents/upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return data;
  },

  delete: async (documentId: string, projectId: string): Promise<void> => {
    await api.delete(`/projects/${projectId}/documents/${documentId}`);
  },

  getContent: async (
    projectId: string,
    documentId: string
  ): Promise<{ content: string; truncated: boolean }> => {
    const { data } = await api.get<{
      content: string;
      truncated: boolean;
    }>(`/projects/${projectId}/documents/${documentId}/content`);
    return data;
  },
};

// ============ Retrieval API ============

export interface RetrievedChunk {
  chunk_id: string;
  document_id: string;
  content: string;
  score: number;
  metadata: Record<string, any>;
}

export interface RetrievalQueryRequest {
  project_id: string;
  query: string;
  top_k?: number;
  overrides?: RetrievalOverrides;
}

export interface RetrievalQueryResponse {
  project_id: string;
  query: string;
  retrieval_strategy: string;
  reranking_strategy: string;
  total: number;
  chunks: RetrievedChunk[];
}

export const ragApi = {
  getOptions: async (
    rag_mode?: RagMode
  ): Promise<{
    rag_mode: RagMode;
    defaults: RagConfig;
    extraction_strategies: string[];
    chunking_strategies: string[];
    retrieval_strategies: string[];
    reranking_strategies: string[];
    graph_indexing?: Record<string, unknown>;
    chunking_params: Record<string, Record<string, number | null>>;
    retrieval_params: Record<string, Record<string, unknown>>;
  }> => {
    const { data } = await api.get('/rag/options', {
      params: rag_mode ? { rag_mode } : undefined,
    });
    return data;
  },
};

export const retrievalApi = {
  query: async (request: RetrievalQueryRequest): Promise<RetrievalQueryResponse> => {
    const { data } = await api.post<RetrievalQueryResponse>('/retrieval/query', request);
    return data;
  },
};

// ============ Admin API ============

export interface AdminUserStats {
  user_id: string;
  email: string;
  role: string;
  project_count: number;
  document_count: number;
  created_at: string;
}

export interface AdminSystemStats {
  users: {
    total: number
    infra_admins?: number
    admins: number
    regular: number
  };
  projects: number;
  documents: { total: number; by_status: Record<string, number> };
}

export interface AdminDocument {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: string;
  chunk_count: number;
  project_id: string;
  project_name: string;
  owner_email: string;
  created_at: string;
}

export const adminApi = {
  getStats: async (): Promise<AdminSystemStats> => {
    const { data } = await api.get<AdminSystemStats>('/admin/stats');
    return data;
  },

  getAllUserStats: async (): Promise<AdminUserStats[]> => {
    const { data } = await api.get<AdminUserStats[]>('/admin/users/stats/all');
    return data;
  },

  listUsers: async (): Promise<User[]> => {
    const { data } = await api.get<User[]>('/admin/users');
    return data;
  },

  createUser: async (user: { email: string; password: string; role: string }): Promise<User> => {
    const { data } = await api.post<User>('/admin/users', user);
    return data;
  },

  updateUserPassword: async (userId: string, password: string): Promise<User> => {
    const { data } = await api.patch<User>(`/admin/users/${userId}`, { password });
    return data;
  },

  updateUserRole: async (userId: string, role: string): Promise<User> => {
    const { data } = await api.patch<User>(`/admin/users/${userId}/role?role=${role}`);
    return data;
  },

  deleteUser: async (userId: string): Promise<void> => {
    await api.delete(`/admin/users/${userId}`);
  },

  listDocuments: async (): Promise<AdminDocument[]> => {
    const { data } = await api.get<AdminDocument[]>('/admin/documents');
    return data;
  },

  deleteDocument: async (documentId: string): Promise<void> => {
    await api.delete(`/admin/documents/${documentId}`);
  },
};

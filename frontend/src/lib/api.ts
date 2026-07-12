import axios, { type AxiosError, type AxiosInstance } from 'axios';
import type {
  DocumentStatus,
  GraphBackend,
  GraphIndexState,
  RagConfig,
  RagMode,
  RetrievalOverrides,
} from './rag-types';

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
  graph_index_status: GraphIndexState | null;
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
    if (Array.isArray(data)) {
      return data;
    }
    return data.projects ?? [];
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
    rag_mode: RagMode,
    graph_backend?: GraphBackend
  ): Promise<{ rag_mode: RagMode; message: string; documents_queued: number }> => {
    const { data } = await api.patch<{ rag_mode: RagMode; message: string; documents_queued: number }>(
      `/projects/${id}/rag-mode`,
      { rag_mode, graph_backend }
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
    rag_mode?: RagMode,
    graph_backend?: GraphBackend
  ): Promise<{
    rag_mode: RagMode;
    graph_backend?: GraphBackend;
    defaults: RagConfig;
    extraction_strategies: string[];
    chunking_strategies: string[];
    retrieval_strategies: string[];
    reranking_strategies: string[];
    graph_indexing?: Record<string, unknown>;
    chunking_params?: Record<string, Record<string, number | null>>;
    retrieval_params: Record<string, Record<string, unknown>>;
  }> => {
    const params: Record<string, string> = {};
    if (rag_mode) params.rag_mode = rag_mode;
    if (graph_backend) params.graph_backend = graph_backend;
    const { data } = await api.get('/rag/options', {
      params: Object.keys(params).length ? params : undefined,
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

// ============ Chat API ============

export interface ChatCitation {
  index: number;
  chunk_id: string;
  document_id: string;
  content: string;
  score: number;
  filename?: string | null;
  metadata?: Record<string, unknown>;
}

export interface ChatQueryRequest {
  project_id: string;
  query: string;
  session_id?: string | null;
  top_k?: number;
  overrides?: RetrievalOverrides;
  persist?: boolean;
}

export interface ChatQueryResponse {
  project_id: string;
  query: string;
  answer: string;
  citations: ChatCitation[];
  retrieval_strategy: string;
  reranking_strategy: string;
  session_id?: string | null;
  turn_id?: string | null;
  model?: string | null;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  empty_retrieval: boolean;
}

export interface ChatSession {
  id: string;
  project_id: string;
  user_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  turn_count?: number | null;
}

export interface ChatTurn {
  id: string;
  session_id: string;
  role: string;
  content: string;
  citations?: ChatCitation[] | null;
  retrieval_strategy?: string | null;
  reranking_strategy?: string | null;
  model?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  latency_ms?: number | null;
  created_at: string;
}

export type ChatStreamHandlers = {
  onSession?: (sessionId: string) => void;
  onStatus?: (stage: string) => void;
  onCitations?: (citations: ChatCitation[], meta: { retrieval_strategy: string; reranking_strategy: string }) => void;
  onToken?: (token: string) => void;
  onDebug?: (payload: Record<string, unknown>) => void;
  onDone?: (payload: Record<string, unknown>) => void;
  onPersisted?: (sessionId: string, turnId: string) => void;
  onError?: (detail: string) => void;
  signal?: AbortSignal;
};

export const chatApi = {
  query: async (request: ChatQueryRequest): Promise<ChatQueryResponse> => {
    const { data } = await api.post<ChatQueryResponse>('/chat/query', request);
    return data;
  },

  listSessions: async (projectId: string): Promise<ChatSession[]> => {
    const { data } = await api.get<{ sessions: ChatSession[]; total: number }>(
      '/chat/sessions',
      { params: { project_id: projectId } }
    );
    return data.sessions;
  },

  createSession: async (projectId: string, title?: string): Promise<ChatSession> => {
    const { data } = await api.post<ChatSession>('/chat/sessions', {
      project_id: projectId,
      title,
    });
    return data;
  },

  deleteSession: async (sessionId: string): Promise<void> => {
    await api.delete(`/chat/sessions/${sessionId}`);
  },

  listTurns: async (sessionId: string): Promise<ChatTurn[]> => {
    const { data } = await api.get<{ session_id: string; turns: ChatTurn[] }>(
      `/chat/sessions/${sessionId}/turns`
    );
    return data.turns;
  },

  stream: async (request: ChatQueryRequest, handlers: ChatStreamHandlers): Promise<void> => {
    const { fetchEventSource } = await import('@microsoft/fetch-event-source');
    const token = localStorage.getItem('access_token');
    await fetchEventSource('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(request),
      signal: handlers.signal,
      openWhenHidden: true,
      onmessage(msg) {
        if (!msg.data) return;
        let data: Record<string, unknown>;
        try {
          data = JSON.parse(msg.data);
        } catch {
          return;
        }
        switch (msg.event) {
          case 'session':
            if (data.session_id) handlers.onSession?.(String(data.session_id));
            break;
          case 'status':
            if (data.stage) handlers.onStatus?.(String(data.stage));
            break;
          case 'citations':
            handlers.onCitations?.(
              (data.citations as ChatCitation[]) || [],
              {
                retrieval_strategy: String(data.retrieval_strategy || ''),
                reranking_strategy: String(data.reranking_strategy || ''),
              }
            );
            break;
          case 'token':
            if (typeof data.content === 'string') handlers.onToken?.(data.content);
            break;
          case 'debug':
            handlers.onDebug?.(data);
            break;
          case 'done':
            handlers.onDone?.(data);
            break;
          case 'persisted':
            handlers.onPersisted?.(
              String(data.session_id || ''),
              String(data.turn_id || '')
            );
            break;
          case 'error':
            handlers.onError?.(String(data.detail || 'Chat stream error'));
            break;
          default:
            break;
        }
      },
      onerror(err) {
        handlers.onError?.(err instanceof Error ? err.message : 'Stream failed');
        throw err;
      },
    });
  },
};

// ============ Website crawl / bulk / suggestions (Phase 4) ============

export interface JobProgressEvent {
  event: string;
  stage?: string;
  message?: string;
  progress?: number;
  pages_found?: number;
  pages_processed?: number;
  document_id?: string;
  document_ids?: string[];
  documents_succeeded?: number;
  documents_failed?: number;
  [key: string]: unknown;
}

export type JobStreamHandlers = {
  onSnapshot?: (ev: JobProgressEvent) => void;
  onProgress?: (ev: JobProgressEvent) => void;
  onClose?: () => void;
  onError?: (detail: string) => void;
  signal?: AbortSignal;
};

async function streamJobEvents(jobId: string, handlers: JobStreamHandlers): Promise<void> {
  const { fetchEventSource } = await import('@microsoft/fetch-event-source');
  const token = localStorage.getItem('access_token');
  await fetchEventSource(`/api/jobs/${jobId}/events`, {
    method: 'GET',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    signal: handlers.signal,
    openWhenHidden: true,
    onmessage(msg) {
      if (!msg.data) return;
      let data: JobProgressEvent;
      try {
        data = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (msg.event === 'snapshot') handlers.onSnapshot?.(data);
      else if (msg.event === 'progress') handlers.onProgress?.(data);
      else if (msg.event === 'close') handlers.onClose?.();
      else if (msg.event === 'error') handlers.onError?.(String(data.detail || 'Job error'));
    },
    onerror(err) {
      handlers.onError?.(err instanceof Error ? err.message : 'Job stream failed');
      throw err;
    },
  });
}

export const websiteApi = {
  crawl: async (
    projectId: string,
    body: {
      url: string;
      max_depth?: number;
      max_pages?: number;
      exclude_patterns?: string[];
    }
  ): Promise<{ job_id: string; status: string; project_id: string }> => {
    const { data } = await api.post(`/projects/${projectId}/crawl`, body);
    return data;
  },
  streamJob: streamJobEvents,
};

export const bulkApi = {
  importPack: async (
    projectId: string,
    file: File
  ): Promise<{ job_id: string; status: string; project_id: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const { data } = await api.post(`/projects/${projectId}/bulk-import`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return data;
  },
  exportPack: async (projectId: string): Promise<Blob> => {
    const { data } = await api.get(`/projects/${projectId}/export`, {
      responseType: 'blob',
    });
    return data;
  },
  streamJob: streamJobEvents,
};

export const suggestionsApi = {
  project: async (projectId: string, count = 5): Promise<string[]> => {
    const { data } = await api.get<{ questions: string[] }>(
      `/projects/${projectId}/suggestions`,
      { params: { count } }
    );
    return data.questions;
  },
  followup: async (
    projectId: string,
    query: string,
    answer: string,
    count = 3
  ): Promise<string[]> => {
    const { data } = await api.post<{ questions: string[] }>('/chat/suggestions/followup', {
      project_id: projectId,
      query,
      answer,
      count,
    });
    return data.questions;
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

export interface AdminDocumentSummary {
  id: string;
  filename: string;
  status: string;
  size_bytes: number;
  chunk_count: number;
  created_at: string;
}

export interface AdminProjectSummary {
  id: string;
  name: string;
  description: string | null;
  rag_mode: string;
  document_count: number;
  created_at: string;
  documents: AdminDocumentSummary[];
}

export interface AdminUserProjectsResponse {
  user_id: string;
  email: string;
  role: string;
  projects: AdminProjectSummary[];
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

  getUserProjects: async (userId: string): Promise<AdminUserProjectsResponse> => {
    const { data } = await api.get<AdminUserProjectsResponse>(
      `/admin/users/${userId}/projects`
    );
    return data;
  },

  deleteProject: async (projectId: string): Promise<void> => {
    await api.delete(`/admin/projects/${projectId}`);
  },

  listDocuments: async (): Promise<AdminDocument[]> => {
    const { data } = await api.get<AdminDocument[]>('/admin/documents');
    return data;
  },

  deleteDocument: async (documentId: string): Promise<void> => {
    await api.delete(`/admin/documents/${documentId}`);
  },
};

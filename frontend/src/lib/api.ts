import axios, { type AxiosError, type AxiosInstance } from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

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
  password: string;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface User {
  id: string;
  email: string;
  role: 'ADMIN' | 'USER';
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

  update: async (id: string, project: Partial<CreateProject>): Promise<Project> => {
    const { data } = await api.patch<Project>(`/projects/${id}`, project);
    return data;
  },

  delete: async (id: string): Promise<void> => {
    await api.delete(`/projects/${id}`);
  },
};

// ============ Documents API ============

export interface Document {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  chunk_count: number;
  project_id: string;
  created_at: string;
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
};

// ============ Sessions API ============

export interface Session {
  id: string;
  name: string;
  project_id: string;
  user_id: string;
  created_at: string;
  updated_at: string;
}

export interface SessionListResponse {
  sessions: Session[];
  total: number;
}

export interface CreateSession {
  name: string;
  project_id: string;
}

export const sessionsApi = {
  list: async (projectId: string): Promise<Session[]> => {
    const { data } = await api.get<SessionListResponse>(`/sessions/project/${projectId}`);
    return data.sessions;
  },

  create: async (session: CreateSession): Promise<Session> => {
    const { data } = await api.post<Session>('/sessions', session);
    return data;
  },

  get: async (id: string): Promise<Session> => {
    const { data } = await api.get<Session>(`/sessions/${id}`);
    return data;
  },

  delete: async (id: string): Promise<void> => {
    await api.delete(`/sessions/${id}`);
  },
};

// ============ Chat API ============

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
}

export const chatApi = {
  getHistory: async (sessionId: string): Promise<ChatMessage[]> => {
    const { data } = await api.get<ChatMessage[]>(`/chat/${sessionId}/history`);
    return data;
  },

  createWebSocket: (sessionId: string): WebSocket => {
    const token = localStorage.getItem('access_token');
    
    // Determine WebSocket URL based on API_BASE_URL
    let wsUrl: string;
    
    if (API_BASE_URL.startsWith('http')) {
      // API_BASE_URL is absolute (e.g., http://localhost:8000/api)
      const apiUrl = new URL(API_BASE_URL);
      const wsProtocol = apiUrl.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = `${wsProtocol}//${apiUrl.host}${apiUrl.pathname}/chat/ws/${sessionId}?token=${token}`;
    } else {
      // API_BASE_URL is relative (e.g., /api) - use current host
      const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = `${wsProtocol}//${window.location.host}${API_BASE_URL}/chat/ws/${sessionId}?token=${token}`;
    }
    
    return new WebSocket(wsUrl);
  },
};

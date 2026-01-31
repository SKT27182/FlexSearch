import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { authApi, type User } from '@/lib/api';
import { useProjectStore } from './project';

interface AuthState {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  isLoading: boolean;
  isInitialized: boolean;  // Track if initial auth check is done

  // Actions
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
  loadUser: () => Promise<void>;
  setTokens: (access: string, refresh: string) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      accessToken: null,
      refreshToken: null,
      isLoading: false,
      isInitialized: false,

      login: async (email: string, password: string) => {
        set({ isLoading: true });
        try {
          const tokens = await authApi.login({ email, password });
          localStorage.setItem('access_token', tokens.access_token);
          localStorage.setItem('refresh_token', tokens.refresh_token);
          set({
            accessToken: tokens.access_token,
            refreshToken: tokens.refresh_token,
          });
          await get().loadUser();
        } finally {
          set({ isLoading: false });
        }
      },

      register: async (email: string, password: string) => {
        set({ isLoading: true });
        try {
          await authApi.register({ email, password });
          // After registration, login automatically
          await get().login(email, password);
        } finally {
          set({ isLoading: false });
        }
      },

      logout: () => {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        
        // Clear project store state to prevent data leakage between users
        useProjectStore.getState().reset();
        
        set({
          user: null,
          accessToken: null,
          refreshToken: null,
          isInitialized: true,
        });
      },

      loadUser: async () => {
        const token = localStorage.getItem('access_token');
        if (!token) {
          set({ isInitialized: true, user: null });
          return;
        }

        try {
          set({ isLoading: true });
          const user = await authApi.getMe();
          set({ user, isInitialized: true });
        } catch {
          get().logout();
        } finally {
          set({ isLoading: false });
        }
      },

      setTokens: (access: string, refresh: string) => {
        localStorage.setItem('access_token', access);
        localStorage.setItem('refresh_token', refresh);
        set({
          accessToken: access,
          refreshToken: refresh,
        });
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
      }),
    }
  )
);

// Derived selector for isAuthenticated
export const useIsAuthenticated = () => 
  useAuthStore((state) => state.user !== null);

import { create } from 'zustand';
import { authApi, setAccessToken, type User } from '@/lib/api';
import { useProjectStore } from './project';

interface AuthState {
  user: User | null;
  accessToken: string | null;
  isLoading: boolean;
  isInitialized: boolean;  // Track if initial auth check is done

  // Actions
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, name: string, password: string) => Promise<void>;
  logout: () => void;
  loadUser: () => Promise<void>;
}

export const useAuthStore = create<AuthState>()(
    (set, get) => ({
      user: null,
      accessToken: null,
      isLoading: false,
      isInitialized: false,

      login: async (email: string, password: string) => {
        set({ isLoading: true });
        try {
          const tokens = await authApi.login({ email, password });
          setAccessToken(tokens.access_token);
          set({
            accessToken: tokens.access_token,
          });
          await get().loadUser();
        } finally {
          set({ isLoading: false });
        }
      },

      register: async (email: string, name: string, password: string) => {
        set({ isLoading: true });
        try {
          await authApi.register({ email, name, password });
          await get().login(email, password);
        } finally {
          set({ isLoading: false });
        }
      },

      logout: () => {
        setAccessToken(null);
        
        // Clear project store state to prevent data leakage between users
        useProjectStore.getState().reset();
        
        set({
          user: null,
          accessToken: null,
          isInitialized: true,
        });
      },

      loadUser: async () => {
        if (!get().accessToken) {
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

    })
);

if (typeof window !== 'undefined') {
  window.addEventListener('flexsearch:unauthorized', () => {
    useAuthStore.getState().logout();
    if (window.location.pathname !== '/login') window.location.assign('/login');
  });
}

// Derived selector for isAuthenticated
export const useIsAuthenticated = () => 
  useAuthStore((state) => state.user !== null);

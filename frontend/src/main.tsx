import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import App from './App.tsx';
import { ThemeProvider } from '@/components/theme-provider';

// Major upgrade: remove every legacy persisted credential before bootstrapping.
localStorage.removeItem('access_token');
localStorage.removeItem('refresh_token');
localStorage.removeItem('auth-storage');

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider defaultTheme="dark" storageKey="flexsearch-theme">
      <App />
    </ThemeProvider>
  </StrictMode>
);

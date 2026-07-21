import { useEffect, useState } from 'react'
import { applyAccentTokens, loadAccentId } from '@/lib/accent-presets'
import { ThemeProviderContext, type Theme } from '@/hooks/useTheme'

interface ThemeProviderProps {
  children: React.ReactNode
  defaultTheme?: Theme
  storageKey?: string
}

export function ThemeProvider({
  children,
  defaultTheme = 'dark',
  storageKey = 'flexsearch-theme',
}: ThemeProviderProps) {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem(storageKey) as Theme) || defaultTheme
  )

  useEffect(() => {
    const root = window.document.documentElement
    root.classList.remove('light', 'dark')
    let resolved: 'light' | 'dark' = 'dark'
    if (theme === 'system') {
      resolved = window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
      root.classList.add(resolved)
    } else {
      resolved = theme
      root.classList.add(theme)
    }
    applyAccentTokens(loadAccentId(), resolved)
  }, [theme])

  const value = {
    theme,
    setTheme: (next: Theme) => {
      localStorage.setItem(storageKey, next)
      setTheme(next)
    },
  }

  return (
    <ThemeProviderContext.Provider value={value}>
      {children}
    </ThemeProviderContext.Provider>
  )
}

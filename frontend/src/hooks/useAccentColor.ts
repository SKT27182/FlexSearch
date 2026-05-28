import { useCallback, useEffect, useState } from 'react'
import { useTheme } from '@/components/theme-provider'
import {
  type AccentId,
  applyAccentTokens,
  loadAccentId,
  saveAccentId,
} from '@/lib/accent-presets'

function resolveThemeMode(theme: string): 'light' | 'dark' {
  if (theme === 'dark') return 'dark'
  if (theme === 'light') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function useAccentColor() {
  const { theme } = useTheme()
  const [accentId, setAccentId] = useState<AccentId>(loadAccentId)

  const apply = useCallback(
    (id: AccentId) => {
      applyAccentTokens(id, resolveThemeMode(theme))
    },
    [theme]
  )

  useEffect(() => {
    apply(accentId)
  }, [accentId, apply])

  const setAccent = useCallback(
    (id: AccentId) => {
      saveAccentId(id)
      setAccentId(id)
      applyAccentTokens(id, resolveThemeMode(theme))
    },
    [theme]
  )

  return { accentId, setAccent }
}

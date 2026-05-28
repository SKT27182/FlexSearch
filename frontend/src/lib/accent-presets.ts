export type AccentId = 'sky' | 'violet' | 'emerald' | 'amber' | 'red'

export const ACCENT_STORAGE_KEY = 'app-accent'

export interface AccentTokens {
  primary: string
  primaryForeground: string
  accent: string
  accentForeground: string
  ring: string
}

export interface AccentPreset {
  id: AccentId
  label: string
  swatch: string
  light: AccentTokens
  dark: AccentTokens
}

export const ACCENT_PRESETS: AccentPreset[] = [
  {
    id: 'sky',
    label: 'Sky',
    swatch: '#38bdf8',
    light: {
      primary: 'oklch(0.55 0.14 230)',
      primaryForeground: 'oklch(0.985 0 0)',
      accent: 'oklch(0.6 0.12 230)',
      accentForeground: 'oklch(0.985 0 0)',
      ring: 'oklch(0.55 0.14 230)',
    },
    dark: {
      primary: 'oklch(0.7 0.14 230)',
      primaryForeground: 'oklch(0.145 0 0)',
      accent: 'oklch(0.55 0.12 230)',
      accentForeground: 'oklch(0.985 0 0)',
      ring: 'oklch(0.7 0.14 230)',
    },
  },
  {
    id: 'violet',
    label: 'Violet',
    swatch: '#8b5cf6',
    light: {
      primary: 'oklch(0.5 0.2 290)',
      primaryForeground: 'oklch(0.985 0 0)',
      accent: 'oklch(0.55 0.18 290)',
      accentForeground: 'oklch(0.985 0 0)',
      ring: 'oklch(0.5 0.2 290)',
    },
    dark: {
      primary: 'oklch(0.65 0.2 290)',
      primaryForeground: 'oklch(0.145 0 0)',
      accent: 'oklch(0.55 0.18 290)',
      accentForeground: 'oklch(0.985 0 0)',
      ring: 'oklch(0.65 0.2 290)',
    },
  },
  {
    id: 'emerald',
    label: 'Emerald',
    swatch: '#10b981',
    light: {
      primary: 'oklch(0.55 0.15 160)',
      primaryForeground: 'oklch(0.985 0 0)',
      accent: 'oklch(0.6 0.13 160)',
      accentForeground: 'oklch(0.985 0 0)',
      ring: 'oklch(0.55 0.15 160)',
    },
    dark: {
      primary: 'oklch(0.7 0.15 160)',
      primaryForeground: 'oklch(0.145 0 0)',
      accent: 'oklch(0.55 0.13 160)',
      accentForeground: 'oklch(0.985 0 0)',
      ring: 'oklch(0.7 0.15 160)',
    },
  },
  {
    id: 'amber',
    label: 'Amber',
    swatch: '#f59e0b',
    light: {
      primary: 'oklch(0.65 0.16 75)',
      primaryForeground: 'oklch(0.2 0 0)',
      accent: 'oklch(0.7 0.14 75)',
      accentForeground: 'oklch(0.2 0 0)',
      ring: 'oklch(0.65 0.16 75)',
    },
    dark: {
      primary: 'oklch(0.75 0.16 75)',
      primaryForeground: 'oklch(0.145 0 0)',
      accent: 'oklch(0.65 0.14 75)',
      accentForeground: 'oklch(0.985 0 0)',
      ring: 'oklch(0.75 0.16 75)',
    },
  },
  {
    id: 'red',
    label: 'Red',
    swatch: '#ef4444',
    light: {
      primary: 'oklch(0.55 0.2 25)',
      primaryForeground: 'oklch(0.985 0 0)',
      accent: 'oklch(0.6 0.18 25)',
      accentForeground: 'oklch(0.985 0 0)',
      ring: 'oklch(0.55 0.2 25)',
    },
    dark: {
      primary: 'oklch(0.65 0.2 25)',
      primaryForeground: 'oklch(0.145 0 0)',
      accent: 'oklch(0.55 0.18 25)',
      accentForeground: 'oklch(0.985 0 0)',
      ring: 'oklch(0.65 0.2 25)',
    },
  },
]

export const DEFAULT_ACCENT_ID: AccentId = 'sky'

export function loadAccentId(): AccentId {
  try {
    const stored = localStorage.getItem(ACCENT_STORAGE_KEY) as AccentId | null
    if (stored && ACCENT_PRESETS.some((p) => p.id === stored)) {
      return stored
    }
  } catch {
    /* ignore */
  }
  return DEFAULT_ACCENT_ID
}

export function saveAccentId(id: AccentId): void {
  try {
    localStorage.setItem(ACCENT_STORAGE_KEY, id)
  } catch {
    /* ignore */
  }
}

export function applyAccentTokens(id: AccentId, mode: 'light' | 'dark'): void {
  const preset = ACCENT_PRESETS.find((p) => p.id === id) ?? ACCENT_PRESETS[0]
  const tokens = mode === 'dark' ? preset.dark : preset.light
  const root = document.documentElement

  root.style.setProperty('--primary', tokens.primary)
  root.style.setProperty('--accent', tokens.accent)
  root.style.setProperty('--color-primary', tokens.primary)
  root.style.setProperty('--color-primary-foreground', tokens.primaryForeground)
  root.style.setProperty('--color-accent', tokens.accent)
  root.style.setProperty('--color-accent-foreground', tokens.accentForeground)
  root.style.setProperty('--color-ring', tokens.ring)
  root.dataset.accent = id
}

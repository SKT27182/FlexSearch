import { cn } from '@/lib/utils'
import { ACCENT_PRESETS } from '@/lib/accent-presets'
import { useAccentColor } from '@/hooks/useAccentColor'

export function AccentPicker() {
  const { accentId, setAccent } = useAccentColor()

  return (
    <div className="flex flex-wrap gap-2">
      {ACCENT_PRESETS.map((preset) => (
        <button
          key={preset.id}
          type="button"
          aria-label={`${preset.label} accent`}
          aria-pressed={accentId === preset.id}
          onClick={() => setAccent(preset.id)}
          className={cn(
            'w-8 h-8 rounded-full border-2 transition-colors',
            accentId === preset.id
              ? 'border-foreground ring-2 ring-primary ring-offset-2 ring-offset-background'
              : 'border-border hover:border-foreground'
          )}
          style={{ backgroundColor: preset.swatch }}
        />
      ))}
    </div>
  )
}

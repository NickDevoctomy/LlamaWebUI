import { Monitor, Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'

export type ThemePreference = 'light' | 'dark' | 'system'

// Keep in sync with the pre-paint script in index.html.
const STORAGE_KEY = 'llamawebui.theme'

export function readThemePreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system'
  }
}

export function applyThemePreference(preference: ThemePreference) {
  const root = document.documentElement
  if (preference === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', preference)
  try {
    if (preference === 'system') window.localStorage.removeItem(STORAGE_KEY)
    else window.localStorage.setItem(STORAGE_KEY, preference)
  } catch {
    // Storage can be unavailable (private windows); the choice still applies to this page.
  }
}

const options = [
  ['light', Sun, 'Light theme'],
  ['dark', Moon, 'Dark theme'],
  ['system', Monitor, 'System theme'],
] as const

export function ThemeSwitch() {
  const [preference, setPreference] = useState<ThemePreference>(readThemePreference)
  useEffect(() => applyThemePreference(preference), [preference])
  return (
    <div aria-label="Colour theme" className="theme-switch" role="radiogroup">
      {options.map(([value, Icon, label]) => (
        <button
          aria-checked={preference === value}
          aria-label={label}
          key={value}
          onClick={() => { applyThemePreference(value); setPreference(value) }}
          role="radio"
          title={label}
          type="button"
        >
          <Icon size={15} />
        </button>
      ))}
    </div>
  )
}

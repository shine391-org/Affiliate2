export const SETTINGS_SECTIONS = [
  { value: "ai", label: "AI Connections" },
  { value: "localization", label: "Localization Profiles" },
  { value: "tts", label: "TTS / Voices" },
  { value: "services", label: "Service Keys" },
  { value: "addons", label: "Add-ons" },
  { value: "health", label: "System Health" },
]

export function normalizeSettingsSection(value) {
  return SETTINGS_SECTIONS.some((item) => item.value === value) ? value : "ai"
}

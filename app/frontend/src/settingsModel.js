export const SETTINGS_SECTIONS = [
  { value: "ai", label: "Kết nối AI" },
  { value: "localization", label: "Hồ sơ bản địa hóa" },
  { value: "tts", label: "TTS / Giọng đọc" },
  { value: "services", label: "Nguồn dữ liệu" },
  { value: "addons", label: "Tiện ích bổ sung" },
  { value: "health", label: "Tình trạng hệ thống" },
]

export function normalizeSettingsSection(value) {
  return SETTINGS_SECTIONS.some((item) => item.value === value) ? value : "ai"
}

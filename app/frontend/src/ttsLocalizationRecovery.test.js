import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8")
const ai = read("./components/settings/AIProviderSection.jsx")
const tts = read("./components/settings/TTSProviderSection.jsx")
const http = read("./api/http.js")
const profiles = read("./components/settings/LocalizationProfilesSection.jsx")
const expert = read("./components/tools/LocalizationExpertPanel.jsx")
const studio = read("./pages/StudioLocalize.jsx")
const batch = read("./pages/LocalizationBatch.jsx")

test("TTS settings exposes connection test, ElevenLabs voice discovery, and exact-form preview", () => {
  assert.ok(tts.includes('/api/settings/tts/test'))
  assert.ok(tts.includes('/api/settings/tts/voices'))
  assert.ok(tts.includes('/api/tts/preview'))
  assert.ok(tts.includes('Kiểm tra kết nối'))
  assert.ok(tts.includes('Tải danh sách giọng'))
  assert.ok(tts.includes('Nghe thử giọng'))
  assert.ok(tts.includes('https://api.elevenlabs.io/v1'))
  assert.ok(!tts.includes('await audioRef.current.play()'), 'autoplay promises must not leave preview stuck')
  assert.ok(tts.includes('audioRef.current.play().then'))
})

test("localization profile allows pinned Edge and clears provider-scoped TTS state on adoption", () => {
  assert.ok(profiles.includes('edgeCrossProviderOverride'))
  assert.ok(profiles.includes('ghim Edge TTS'))
  assert.ok(profiles.includes('không kế thừa API key, model, Base URL hoặc voice'))
  assert.ok(profiles.includes('Dùng TTS hiện tại cho hồ sơ'))
  assert.ok(profiles.includes('provider active hoặc Edge TTS tự chứa'))
  assert.ok(profiles.includes('speaker_voices: {}'))
  assert.ok(profiles.includes('speaker_reference_audio: {}'))
  assert.ok(profiles.includes('tts_reference_audio: null'))
})

test("expert recovery makes changed TTS resumable without locking the editor", () => {
  assert.ok(expert.includes('/rebind-tts'))
  assert.ok(expert.includes('Dùng TTS hiện tại & tiếp tục'))
  assert.ok(expert.includes('Cấu hình TTS đã thay đổi'))
  assert.ok(expert.includes('không chạy lại tách âm thanh, ASR hoặc dịch thuật'))
  assert.ok(expert.includes('Chạy lại từ '))
  assert.ok(!expert.includes('Chạy lại với cấu hình job'))
  assert.ok(expert.includes('await load()'))
  assert.ok(expert.includes('TTS của job'))
  assert.ok(expert.includes('TTS hiện tại'))
})

test("TTS settings never labels an unprobed provider as READY", () => {
  assert.ok(tts.includes("CẤU HÌNH HỢP LỆ"), 'unverified providers must say configuration is valid')
  assert.ok(tts.includes("connection_verified === false"))
  assert.ok(!tts.includes('{health.ok ? "READY"'), 'READY must depend on connection_verified')
})

test("blob error bodies are decoded before any snackbar message", () => {
  assert.ok(http.includes("export async function apiErrorMessage"))
  assert.ok(http.includes("data instanceof Blob"))
  assert.ok(http.includes("await data.text()"))
  assert.ok(tts.includes("apiErrorMessage as errorMessage"))
  assert.ok(tts.includes("await errorMessage(err"), 'preview/test errors must be awaited')
  assert.ok(!tts.includes("err.response?.data?.detail"), 'raw blob detail access loses provider errors')
})

test("batch dialog has visible X and Đóng controls without cancellation calls", () => {
  assert.ok(studio.includes('CloseOutlinedIcon'))
  assert.ok(studio.includes('aria-label="Đóng hộp thoại bản địa hóa hàng loạt"'))
  assert.ok(studio.includes('>Đóng</Button>'))
  assert.ok(!studio.includes('/cancel'))
  assert.ok(batch.includes('Chọn video'))
  assert.ok(!batch.includes('Select videos'))
})


test("localization C6/C7 model discovery is directly selectable and still free-form", () => {
  assert.ok(profiles.includes("<Autocomplete"))
  assert.ok(profiles.includes('label="Mô hình dịch C6"'))
  assert.ok(profiles.includes('label="Mô hình viết lại thời lượng C7"'))
  assert.ok(profiles.includes("freeSolo"))
  assert.ok(!profiles.includes('localization-model-suggestions'))
})

test("expert panel exposes C7 rewrite evidence", () => {
  assert.ok(expert.includes("C7 · Viết lại thời lượng"))
  assert.ok(expert.includes("dubbingMeta.rewrite_count"))
  assert.ok(expert.includes("rewrite_ai_unavailable"))
  assert.ok(expert.includes("timing_overflow"))
})

test("provider settings remember prior AI/TTS connections and expose selectable models", () => {
  assert.ok(ai.includes("ai_provider_profiles"))
  assert.ok(ai.includes("saved?.model"))
  assert.ok(ai.includes("saved?.base_url"))
  assert.ok(ai.includes("· đã lưu"))
  assert.ok(ai.includes("<InputLabel>Chọn mô hình</InputLabel>"))
  assert.ok(ai.includes("suggestions.map"))
  assert.ok(ai.includes("Nhập ID khác…"))

  assert.ok(tts.includes("tts_provider_profiles"))
  assert.ok(tts.includes("saved?.voice"))
  assert.ok(tts.includes("saved?.model"))
  assert.ok(tts.includes("saved?.base_url"))
  assert.ok(tts.includes("saved?.region"))
  assert.ok(tts.includes("Đã lưu cho provider này"))
  assert.ok(tts.includes("models.map"))
})

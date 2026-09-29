import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8")
const tts = read("./components/settings/TTSProviderSection.jsx")
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

test("localization profile warns immediately when its TTS provider mismatches Settings", () => {
  assert.ok(profiles.includes('Provider không khớp'))
  assert.ok(profiles.includes('Dùng TTS hiện tại cho hồ sơ'))
})

test("expert recovery distinguishes persisted rerun from current-settings rebind", () => {
  assert.ok(expert.includes('/rebind-tts'))
  assert.ok(expert.includes('Chạy lại với cấu hình job'))
  assert.ok(expert.includes('Dùng TTS hiện tại & chạy lại lồng tiếng'))
  assert.ok(expert.includes('TTS của job'))
  assert.ok(expert.includes('TTS hiện tại'))
})

test("batch dialog has visible X and Đóng controls without cancellation calls", () => {
  assert.ok(studio.includes('CloseOutlinedIcon'))
  assert.ok(studio.includes('aria-label="Đóng hộp thoại bản địa hóa hàng loạt"'))
  assert.ok(studio.includes('>Đóng</Button>'))
  assert.ok(!studio.includes('/cancel'))
  assert.ok(batch.includes('Chọn video'))
  assert.ok(!batch.includes('Select videos'))
})

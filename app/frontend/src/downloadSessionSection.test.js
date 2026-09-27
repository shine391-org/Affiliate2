import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const section = readFileSync(
  new URL("./components/settings/DownloadSessionSection.jsx", import.meta.url),
  "utf8",
)

test("Douyin session action calls the backend instead of using a plain href", () => {
  assert.ok(section.includes('http.post("/api/settings/download-session/open-douyin"'))
  assert.ok(section.includes("Mở Douyin để đăng nhập"))
  assert.ok(!section.includes('href="https://www.douyin.com/"'))
})

test("Douyin session action sends the selected browser as authoritative", () => {
  assert.match(
    section,
    /open-douyin[\s\S]*douyin_browser: douyinBrowser,[\s\S]*douyin_cdp_url: cdpUrl/,
  )
})

test("Firefox is available for cookie extraction but never offered as a Douyin CDP browser", () => {
  const douyinChoices = section.match(/const DOUYIN_BROWSERS = \[([\s\S]*?)\n\]/)?.[1] || ""
  const cookieChoices = section.match(/const COOKIE_BROWSERS = \[([\s\S]*?)\n\]/)?.[1] || ""
  assert.ok(!douyinChoices.includes("firefox"))
  assert.ok(cookieChoices.includes("firefox"))
})

test("download session UI renders actual executable, profile, endpoint and CDP stages", () => {
  assert.ok(section.includes("executable_path"))
  assert.ok(section.includes("profile_path"))
  assert.ok(section.includes("cdp_url"))
  assert.ok(section.includes("cdp_http_reachable"))
  assert.ok(section.includes("cdp_websocket_connected"))
  assert.ok(section.includes("douyin.page_reachable"))
})

test("Douyin session action exposes loading, auth-required, authenticated and error states", () => {
  assert.ok(section.includes("opening"))
  assert.ok(section.includes("Đang mở…"))
  assert.ok(section.includes("Cần đăng nhập Douyin trong cửa sổ Edge vừa mở."))
  assert.ok(section.includes("Phiên trình duyệt Douyin đã xác thực và sẵn sàng."))
  assert.ok(section.includes("Không thể mở phiên Douyin"))
  assert.ok(section.includes("launchResult.cdp_error"))
})

test("a connected launch refreshes the full health probe", () => {
  assert.ok(section.includes("if (data.connected)"))
  assert.ok(section.includes("await test({ notify: false })"))
})

test("health messaging preserves independent Douyin and TikTok ownership", () => {
  assert.ok(section.includes("hai cơ chế có thể dùng hai trình duyệt khác nhau"))
  assert.ok(section.includes("Đang chờ trình duyệt"))
  assert.ok(section.includes("Cần đăng nhập Douyin"))
  assert.ok(section.includes("TikTok: Cần đăng nhập"))
})

test("UI separates Douyin browser session from yt-dlp cookie source", () => {
  assert.ok(section.includes("Phiên trình duyệt Douyin"))
  assert.ok(section.includes("Cookie tải xuống / TikTok"))
  assert.ok(section.includes("douyin_browser"))
  assert.ok(section.includes("download_cookie_browser"))
  assert.ok(!section.includes("Download Browser Session"))
})

test("self-repair and TikTok actions call structured backend endpoints", () => {
  assert.ok(section.includes('/api/settings/download-session/repair'))
  assert.ok(section.includes('/api/settings/download-session/open-tiktok'))
  assert.ok(section.includes("Tự sửa tất cả"))
  assert.ok(section.includes("Mở TikTok để đăng nhập"))
  assert.ok(!section.includes('href="https://www.tiktok.com/"'))
})

test("dependency-aware status uses blocking reasons instead of duplicate failures", () => {
  assert.ok(section.includes("blocking_reason"))
  assert.ok(section.includes("Đang chờ trình duyệt"))
  assert.ok(section.includes("Chưa thể kiểm tra cho tới khi trình duyệt chạy"))
  assert.ok(section.includes("Cookie store: Ready"))
  assert.ok(section.includes("TikTok: Cần đăng nhập"))
})


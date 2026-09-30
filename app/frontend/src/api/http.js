import axios from "axios"

// A request sent with responseType:"blob" also delivers its ERRORS as a Blob,
// so err.response.data.detail is undefined and every provider status/message
// (ElevenLabs 401/403/404, unknown model/voice) is silently lost. Decode the
// body before any caller reads it.
export async function decodeApiErrorBody(err) {
  const data = err?.response?.data
  if (!data) return data
  if (typeof data === "string") {
    try { return JSON.parse(data) } catch { return data }
  }
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    const text = await data.text()
    if (!text) return undefined
    try { return JSON.parse(text) } catch { return text }
  }
  return data
}

export async function apiErrorMessage(err, fallback) {
  const body = await decodeApiErrorBody(err)
  const detail = body?.detail
  if (typeof detail === "string" && detail.trim()) return detail
  if (detail?.message) return detail.message
  if (detail) return JSON.stringify(detail)
  if (typeof body === "string" && body.trim()) return body
  return err?.message || fallback
}

const http = axios.create({
  baseURL: window.location.origin,  // works for both dev (localhost:16888) and packaged Electron app
  timeout: 30000,
})

http.interceptors.response.use(
  res => res,
  err => {
    console.error("API error:", err.response?.data || err.message)
    return Promise.reject(err)
  }
)

export default http

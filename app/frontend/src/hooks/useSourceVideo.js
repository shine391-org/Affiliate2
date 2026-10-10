import { useState, useEffect } from "react"
import { useSearchParams } from "react-router-dom"
import http from "../api/http"

/**
 * Load a source (competitor) video from ?source=id URL param.
 * Used by all 3 studio pages when creating "inspired by" content.
 */
export default function useSourceVideo() {
  const [searchParams] = useSearchParams()
  const sourceId = searchParams.get("source")

  const [source, setSource] = useState(null)
  const [sourceLoading, setSourceLoading] = useState(false)

  useEffect(() => {
    if (!sourceId) { setSource(null); setSourceLoading(false); return undefined }
    let alive = true
    setSource(null)
    setSourceLoading(true)
    http.get(`/api/downloaded/${sourceId}`)
      .then(res => {
        if (!alive) return
        const data = res.data
        if (typeof data.insights_json === "string") {
          try { data.insights = JSON.parse(data.insights_json) } catch {}
        }
        setSource(data)
      })
      .catch(() => { if (alive) setSource(null) })
      .finally(() => { if (alive) setSourceLoading(false) })
    return () => { alive = false }
  }, [sourceId])

  return { source, sourceLoading, sourceId }
}

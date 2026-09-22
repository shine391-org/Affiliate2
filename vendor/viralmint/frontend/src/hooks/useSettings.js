import { useState, useEffect } from "react"
import http from "../api/http"
import useAppStore from "../store/appStore"

export default function useSettings() {
  const settings = useAppStore((s) => s.settings)
  const setSettings = useAppStore((s) => s.setSettings)
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [loading, setLoading] = useState(!settings)
  const [error, setError] = useState(null)

  const fetchSettings = async () => {
    try {
      setLoading(true)
      setError(null)
      const { data } = await http.get("/api/settings")
      setSettings(data)
    } catch (err) {
      console.error("Failed to fetch settings:", err)
      setError("Failed to load settings")
    } finally {
      setLoading(false)
    }
  }

  const updateSettings = async (updates) => {
    try {
      const { data } = await http.post("/api/settings", updates)
      setSettings(data)
      return data
    } catch (err) {
      console.error("Failed to update settings:", err)
      showSnackbar("Failed to save setting", "error")
      throw err
    }
  }

  useEffect(() => {
    fetchSettings()
  }, [])

  return { settings, loading, error, fetchSettings, updateSettings }
}

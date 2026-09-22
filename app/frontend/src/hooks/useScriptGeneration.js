import { useState } from "react"
import http from "../api/http"
import useAppStore from "../store/appStore"

/**
 * Script generation, polishing, and scene splitting logic.
 * Shared by all 3 studio pages.
 */
export default function useScriptGeneration() {
  const showSnackbar = useAppStore((s) => s.showSnackbar)

  const [script, setScript] = useState("")
  const [scriptInstructions, setScriptInstructions] = useState("")
  const [scriptLoading, setScriptLoading] = useState(false)
  const [scriptGenerated, setScriptGenerated] = useState(false)

  const handleGenerateScript = async (sourceId, aspectRatio = "9:16") => {
    setScriptLoading(true)
    try {
      let res
      if (sourceId) {
        res = await http.post(`/api/downloaded/${sourceId}/generate-script`, {
          aspect_ratio: aspectRatio,
          user_instructions: scriptInstructions.trim() || undefined,
        })
      } else {
        if (!scriptInstructions.trim()) {
          showSnackbar("Enter a topic or instructions for the AI to generate a script", "warning")
          setScriptLoading(false)
          return
        }
        res = await http.post("/api/downloaded/generate-script-from-topic", {
          topic: scriptInstructions.trim(),
          aspect_ratio: aspectRatio,
        })
      }
      setScript(res.data.script || "")
      setScriptGenerated(true)
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Script generation failed", "error")
    } finally {
      setScriptLoading(false)
    }
  }

  const handlePolishScript = async () => {
    if (!script.trim()) {
      showSnackbar("Write a script first before polishing", "warning")
      return
    }
    setScriptLoading(true)
    try {
      const res = await http.post("/api/downloaded/polish-script", { script: script.trim() })
      setScript(res.data.script || script)
      showSnackbar("Script polished successfully", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Script polishing failed", "error")
    } finally {
      setScriptLoading(false)
    }
  }

  const splitIntoScenes = async (mode, aspectRatio = "9:16", sourceId = null) => {
    if (!script.trim()) {
      showSnackbar("Write a script first before splitting into scenes", "warning")
      return null
    }
    setScriptLoading(true)
    try {
      const res = await http.post("/api/generate/split-scenes", {
        script: script.trim(),
        mode,
        aspect_ratio: aspectRatio,
        source_id: sourceId || undefined,
      })
      return res.data.scenes || []
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Scene splitting failed", "error")
      return null
    } finally {
      setScriptLoading(false)
    }
  }

  return {
    script, setScript,
    scriptInstructions, setScriptInstructions,
    scriptLoading, scriptGenerated,
    handleGenerateScript,
    handlePolishScript,
    splitIntoScenes,
  }
}

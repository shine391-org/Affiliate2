import { useState, useEffect, useRef, useCallback } from "react"
import { Box, TextField, IconButton, Paper, Typography, List, ListItemButton, ListItemText } from "@mui/material"
import ArrowUpwardIcon from "@mui/icons-material/ArrowUpward"
import SearchIcon from "@mui/icons-material/Search"
import http from "../../api/http"

// Debounce helper
function useDebounce(value, delay) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

export default function ChatInput({ onSend, disabled }) {
  const [text, setText] = useState("")
  const [suggestions, setSuggestions] = useState([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(-1)
  const inputRef = useRef(null)
  const suggestionsRef = useRef(null)

  // Extract a potential niche query from the input
  // Trigger suggestions when user types "scout <query>" or just a niche keyword
  const extractQuery = useCallback((input) => {
    const trimmed = input.trim().toLowerCase()
    // Match patterns like "scout personal finance", "search for cooking", etc.
    const scoutMatch = trimmed.match(/(?:scout|search|find|explore|discover)\s+(?:for\s+|about\s+)?(.{2,})/i)
    if (scoutMatch) return scoutMatch[1]
    // If input is 3+ words without a command prefix, it might be a niche
    // Don't suggest for general chat messages
    return null
  }, [])

  const query = extractQuery(text)
  const debouncedQuery = useDebounce(query, 300)

  // Fetch YouTube suggestions
  useEffect(() => {
    if (!debouncedQuery || debouncedQuery.length < 2) {
      setSuggestions([])
      setShowSuggestions(false)
      return
    }

    let cancelled = false
    const fetchSuggestions = async () => {
      try {
        const { data } = await http.get("/api/scout/suggest", { params: { q: debouncedQuery } })
        if (!cancelled && data.suggestions?.length > 0) {
          setSuggestions(data.suggestions.slice(0, 6))
          setShowSuggestions(true)
          setSelectedIndex(-1)
        } else if (!cancelled) {
          setSuggestions([])
          setShowSuggestions(false)
        }
      } catch {
        if (!cancelled) {
          setSuggestions([])
          setShowSuggestions(false)
        }
      }
    }
    fetchSuggestions()
    return () => { cancelled = true }
  }, [debouncedQuery])

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText("")
    setSuggestions([])
    setShowSuggestions(false)
  }

  const handleSelectSuggestion = (suggestion) => {
    // Replace the query part in the text with the suggestion
    const trimmed = text.trim()
    const scoutMatch = trimmed.match(/^(.*?(?:scout|search|find|explore|discover)\s+(?:for\s+|about\s+)?)/i)
    if (scoutMatch) {
      setText(scoutMatch[1] + suggestion)
    } else {
      setText(`Scout ${suggestion}`)
    }
    setShowSuggestions(false)
    setSuggestions([])
    // Focus back on input
    inputRef.current?.querySelector("input, textarea")?.focus()
  }

  const handleKeyDown = (e) => {
    if (showSuggestions && suggestions.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setSelectedIndex((prev) => (prev + 1) % suggestions.length)
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setSelectedIndex((prev) => (prev <= 0 ? suggestions.length - 1 : prev - 1))
        return
      }
      if (e.key === "Enter" && !e.shiftKey && selectedIndex >= 0) {
        e.preventDefault()
        handleSelectSuggestion(suggestions[selectedIndex])
        return
      }
      if (e.key === "Escape") {
        setShowSuggestions(false)
        return
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // Close suggestions when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (suggestionsRef.current && !suggestionsRef.current.contains(e.target)) {
        setShowSuggestions(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [])

  const canSend = text.trim() && !disabled

  return (
    <Box
      sx={{
        maxWidth: 720,
        mx: "auto",
        width: "100%",
        px: 1,
        pb: 2,
        pt: 1,
        position: "relative",
      }}
    >
      {/* YouTube Suggest Dropdown */}
      {showSuggestions && suggestions.length > 0 && (
        <Paper
          ref={suggestionsRef}
          elevation={4}
          sx={{
            position: "absolute",
            bottom: "100%",
            left: 8,
            right: 8,
            mb: 0.5,
            borderRadius: 2,
            overflow: "hidden",
            zIndex: 10,
          }}
        >
          <Box sx={{ px: 1.5, py: 0.75, borderBottom: 1, borderColor: "divider", display: "flex", alignItems: "center", gap: 0.5 }}>
            <SearchIcon sx={{ fontSize: 14, color: "text.secondary" }} />
            <Typography variant="caption" color="text.secondary" fontWeight={600}>
              YouTube Search Suggestions
            </Typography>
          </Box>
          <List dense sx={{ py: 0.5 }}>
            {suggestions.map((s, i) => (
              <ListItemButton
                key={i}
                selected={i === selectedIndex}
                onClick={() => handleSelectSuggestion(s)}
                sx={{ py: 0.5, px: 1.5, borderRadius: 1, mx: 0.5 }}
              >
                <ListItemText
                  primary={s}
                  primaryTypographyProps={{ fontSize: "0.85rem" }}
                />
              </ListItemButton>
            ))}
          </List>
        </Paper>
      )}

      <Box
        sx={{
          display: "flex",
          alignItems: "flex-end",
          gap: 1,
          bgcolor: "background.paper",
          border: 2,
          borderColor: "divider",
          borderRadius: 4,
          px: 2,
          py: 1,
          transition: "all 0.2s cubic-bezier(0.4, 0, 0.2, 1)",
          boxShadow: (theme) => theme.customShadows?.md,
          "&:focus-within": {
            borderColor: "primary.main",
            boxShadow: (theme) => `${theme.customShadows?.lg}, ${theme.customShadows?.glow}`,
          },
        }}
      >
        <TextField
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Message ViralMint..."
          disabled={disabled}
          multiline
          maxRows={6}
          fullWidth
          size="small"
          variant="standard"
          slotProps={{ input: { disableUnderline: true } }}
          sx={{
            "& .MuiInputBase-root": {
              fontSize: "0.925rem",
              lineHeight: 1.6,
              py: 0.5,
            },
          }}
        />
        <IconButton
          onClick={handleSend}
          disabled={!canSend}
          size="small"
          sx={{
            background: canSend ? "linear-gradient(135deg, #c96442, #e88a5a)" : undefined,
            bgcolor: canSend ? undefined : "action.hover",
            color: canSend ? "#fff" : "text.disabled",
            borderRadius: 2.5,
            width: 36,
            height: 36,
            mb: 0.25,
            transition: "all 0.15s ease",
            boxShadow: canSend ? (theme) => theme.customShadows?.sm : "none",
            "&:hover": {
              background: canSend ? "linear-gradient(135deg, #b85838, #d47a4e)" : undefined,
              boxShadow: canSend ? (theme) => `${theme.customShadows?.md}, ${theme.customShadows?.glow}` : "none",
              transform: canSend ? "scale(1.05)" : "none",
            },
            "&:active": {
              transform: "scale(0.95)",
            },
          }}
        >
          <ArrowUpwardIcon sx={{ fontSize: 18 }} />
        </IconButton>
      </Box>
    </Box>
  )
}

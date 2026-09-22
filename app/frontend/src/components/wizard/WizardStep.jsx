import { useState } from "react"
import { Box, Typography, Button, TextField, Stack, Link } from "@mui/material"
import OpenInNewIcon from "@mui/icons-material/OpenInNew"

export default function WizardStep({ step, onComplete }) {
  const [inputValue, setInputValue] = useState("")

  if (!step) return null

  const handleSubmit = () => {
    if (inputValue.trim()) {
      onComplete(step.id, inputValue.trim(), step.field)
      setInputValue("")
    }
  }

  return (
    <Box sx={{ py: 2 }}>
      <Typography sx={{ mb: 2, lineHeight: 1.6 }}>{step.instruction}</Typography>

      {step.action === "open_url" && (
        <Stack spacing={1.5}>
          <Link href={step.url} target="_blank" rel="noopener noreferrer" sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            Open in browser <OpenInNewIcon fontSize="small" />
          </Link>
          <Button variant="contained" onClick={() => onComplete(step.id, true)} sx={{ alignSelf: "flex-start" }}>
            Continue
          </Button>
        </Stack>
      )}

      {step.action === "wait_confirm" && (
        <Button variant="contained" onClick={() => onComplete(step.id, true)}>
          {step.confirm_label || "Continue"}
        </Button>
      )}

      {step.action === "text_input" && (
        <Box sx={{ display: "flex", gap: 1 }}>
          <TextField
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder={step.placeholder || ""}
            onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
            size="small"
            fullWidth
            sx={{ "& .MuiOutlinedInput-root": { bgcolor: "background.default" } }}
          />
          <Button variant="contained" onClick={handleSubmit} disabled={!inputValue.trim()}>
            Save
          </Button>
        </Box>
      )}

      {step.action === "oauth_button" && (
        <Button variant="contained" component="a" href={step.endpoint} target="_blank" rel="noopener noreferrer">
          {step.button_label || "Connect"}
        </Button>
      )}

      {step.action === "select" && step.options && (
        <Stack spacing={1}>
          {step.options.map((opt) => (
            <Button
              key={opt.value}
              variant="outlined"
              onClick={() => onComplete(step.id, opt.value, step.field)}
              sx={{ justifyContent: "flex-start", borderColor: "divider", color: "text.primary" }}
            >
              {opt.label}
            </Button>
          ))}
        </Stack>
      )}

      {step.action === "link_guide" && step.links && (
        <Stack spacing={1}>
          {Object.entries(step.links).map(([key, link]) => (
            <Link key={key} href={link.url} target="_blank" rel="noopener noreferrer" sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
              {link.label} <OpenInNewIcon fontSize="small" />
            </Link>
          ))}
          <Button variant="contained" onClick={() => onComplete(step.id, true)} sx={{ alignSelf: "flex-start", mt: 1 }}>
            I have my API key
          </Button>
        </Stack>
      )}

      {step.action === "success" && (
        <Typography sx={{ color: "primary.main", fontWeight: 600 }}>{step.instruction}</Typography>
      )}
    </Box>
  )
}

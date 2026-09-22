import { Stack, Chip } from "@mui/material"

export default function QuickReplyChips({ suggestions, onSelect }) {
  if (!suggestions || suggestions.length === 0) return null

  return (
    <Stack direction="row" spacing={0.75} flexWrap="wrap" sx={{ mb: 1, gap: 0.5 }}>
      {suggestions.map((s, i) => (
        <Chip
          key={i}
          label={s}
          onClick={() => onSelect(s)}
          variant="outlined"
          size="small"
          clickable
          sx={{
            borderColor: "divider",
            color: "text.primary",
            fontSize: "0.82rem",
            "&:hover": {
              bgcolor: "action.selected",
              borderColor: "primary.main",
            },
          }}
        />
      ))}
    </Stack>
  )
}

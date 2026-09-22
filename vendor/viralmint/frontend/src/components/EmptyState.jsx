import { Box, Typography, Button, Stack, alpha, useTheme } from "@mui/material"

/**
 * Shared empty-state visual for any "no content yet" page.
 *
 * Before: every page rolled its own ("No videos yet", "No cron jobs",
 * "No channels connected") with different paddings, icon sizes, and
 * tones. After: one component everywhere, one place to change the
 * empty-state recipe.
 *
 * Visual recipe (matches the warm-glass aesthetic from Phases 1-3):
 *   - Soft accent-tinted icon halo (round, faintly glowing)
 *   - h6 title + caption description
 *   - Optional primary CTA + optional secondary "ghost" link
 *   - Generous vertical padding so empty pages feel intentional
 *     rather than abandoned
 *
 * Props are forgiving — pass only what you need.
 */
export default function EmptyState({
  icon,
  title,
  description,
  primaryAction,    // { label, onClick, startIcon?, disabled? }
  secondaryAction,  // { label, onClick, startIcon? } — rendered as text button
  accentColor,
  compact = false,
  maxWidth = 420,
}) {
  const theme = useTheme()
  const isDark = theme.palette.mode === "dark"
  const accent = accentColor || theme.palette.primary.main

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        py: compact ? 4 : { xs: 6, md: 8 },
        px: 3,
        width: "100%",
      }}
    >
      {icon && (
        <Box
          sx={{
            width: compact ? 56 : 76,
            height: compact ? 56 : 76,
            borderRadius: "50%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            mb: 2,
            color: accent,
            bgcolor: alpha(accent, isDark ? 0.14 : 0.08),
            boxShadow: (t) => `inset 0 0 0 1px ${alpha(accent, 0.22)}, 0 0 32px ${alpha(accent, isDark ? 0.18 : 0.12)}`,
            "& svg": { fontSize: compact ? 26 : 34 },
          }}
        >
          {icon}
        </Box>
      )}
      {title && (
        <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.75, color: "text.primary" }}>
          {title}
        </Typography>
      )}
      {description && (
        <Typography
          variant="body2"
          sx={{ color: "text.secondary", maxWidth, lineHeight: 1.6, mb: 3 }}
        >
          {description}
        </Typography>
      )}
      {(primaryAction || secondaryAction) && (
        <Stack direction="row" spacing={1.5} alignItems="center">
          {primaryAction && (
            <Button
              variant="contained"
              size="medium"
              onClick={primaryAction.onClick}
              startIcon={primaryAction.startIcon}
              disabled={primaryAction.disabled}
            >
              {primaryAction.label}
            </Button>
          )}
          {secondaryAction && (
            <Button
              variant="text"
              size="medium"
              onClick={secondaryAction.onClick}
              startIcon={secondaryAction.startIcon}
              sx={{ color: "text.secondary" }}
            >
              {secondaryAction.label}
            </Button>
          )}
        </Stack>
      )}
    </Box>
  )
}

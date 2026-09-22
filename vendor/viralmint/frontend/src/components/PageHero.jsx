import { Box, Typography, Stack, Chip, IconButton, alpha, useTheme } from "@mui/material"
import ArrowBackIcon from "@mui/icons-material/ArrowBack"
import { useNavigate } from "react-router-dom"

/**
 * Shared hero band rendered at the top of every "destination" page
 * (Settings, Videos, Channels, etc.).
 *
 * Before unification, every page hand-rolled its own header: each used
 * a different gradient color stop, slightly different padding, and a
 * different icon-title-subtitle layout. Future audits stay cheap if
 * every page reaches for this one component.
 *
 * Visual signature (the "refined / Linear-like" lane):
 *   - warm-tinted gradient backdrop (terracotta or a custom accent)
 *   - subtle backdrop-filter blur so anything behind the hero (e.g.
 *     the page content scrolling beneath when overflow-pinned) gains
 *     a glass quality without becoming distracting
 *   - thin divider at the bottom anchors the band to the content
 *
 * Slots:
 *   - icon       — leading icon (h5-sized, primary color by default)
 *   - title      — h5 page title (string or node)
 *   - subtitle   — caption-sized secondary line
 *   - chip       — optional Chip alongside the title
 *   - accentColor — overrides the gradient hue. Defaults to theme primary.
 *   - actions    — right-aligned slot for buttons / toggles / chips
 *
 * Props are forgiving — pass only what you need.
 */
export default function PageHero({
  icon,
  title,
  subtitle,
  chip,
  actions,
  accentColor,
  backTo,           // optional — render leading back-arrow that nav's here
  // Optional price string (e.g. "$0.01", "Free", "from $0.25"). In the BYOK
  // / open-source build there is no per-action billing, so this is rendered
  // as a muted caption next to the subtitle rather than a cloud PriceChip.
  // Tool pages may still pass a usage hint string here; it just renders as
  // plain secondary text.
  price,
  dense = false,
}) {
  const theme = useTheme()
  const navigate = useNavigate()
  const isDark = theme.palette.mode === "dark"
  const accent = accentColor || theme.palette.primary.main

  return (
    <Box
      sx={{
        px: { xs: 2, md: 3 },
        py: dense ? 1.5 : 2.25,
        flexShrink: 0,
        position: "relative",
        borderBottom: 1,
        borderColor: "divider",
        // Warm-glass gradient. The accent stop sits at the top-left
        // corner and fades into the surface color, so the eye gets a
        // hint of color without the band turning into a full-bleed
        // accent panel. In dark mode the second stop is the page
        // surface; in light mode it fades to white for an airier hero.
        background: isDark
          ? `linear-gradient(135deg, ${alpha(accent, 0.14)} 0%, ${alpha(theme.palette.background.paper, 0.72)} 70%)`
          : `linear-gradient(135deg, ${alpha(accent, 0.08)} 0%, ${alpha("#ffffff", 0.78)} 70%)`,
        backdropFilter: "blur(20px) saturate(1.8)",
        WebkitBackdropFilter: "blur(20px) saturate(1.8)",
        // Soft glow on the leading edge — terracotta-tinted ambient
        // light that sells the warm-glass aesthetic without colored
        // shadows on every card downstream.
        "&::before": {
          content: '""',
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          background: `radial-gradient(circle at 0% 0%, ${alpha(accent, isDark ? 0.18 : 0.10)}, transparent 55%)`,
        },
      }}
    >
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="center"
        sx={{ position: "relative", zIndex: 1 }}
      >
        {backTo && (
          <IconButton
            size="small"
            onClick={() => navigate(backTo)}
            sx={{
              color: "text.secondary",
              "&:hover": { color: accent, bgcolor: alpha(accent, 0.08) },
              transition: (t) => `all ${t.customMotion?.duration?.fast || "0.12s"} ${t.customMotion?.easing?.standard || "ease"}`,
            }}
          >
            <ArrowBackIcon fontSize="small" />
          </IconButton>
        )}
        {icon && (
          <Box
            sx={{
              flexShrink: 0,
              width: 40, height: 40,
              borderRadius: 2,
              display: "flex", alignItems: "center", justifyContent: "center",
              bgcolor: alpha(accent, isDark ? 0.18 : 0.10),
              color: accent,
              boxShadow: (t) => `inset 0 0 0 1px ${alpha(accent, 0.18)}, ${t.customShadows?.sm || "none"}`,
              transition: (t) => `all ${t.customMotion?.duration?.base || "0.2s"} ${t.customMotion?.easing?.standard || "ease"}`,
            }}
          >
            {icon}
          </Box>
        )}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            {typeof title === "string"
              ? <Typography variant="h5" sx={{ fontWeight: 700, letterSpacing: "-0.02em" }}>
                  {title}
                </Typography>
              : title
            }
            {chip && (
              <Chip
                label={chip}
                size="small"
                sx={{
                  height: 22, fontSize: "0.7rem", fontWeight: 600,
                  bgcolor: alpha(accent, isDark ? 0.18 : 0.10),
                  color: accent,
                  borderRadius: 1.25,
                }}
              />
            )}
          </Stack>
          {(subtitle || price) && (
            <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" sx={{ mt: 0.25 }}>
              {subtitle && (
                <Typography variant="caption" sx={{ color: "text.secondary" }}>
                  {subtitle}
                </Typography>
              )}
              {price && (
                <Typography variant="caption" sx={{ color: "text.disabled", fontWeight: 600 }}>
                  {price}
                </Typography>
              )}
            </Stack>
          )}
        </Box>
        {actions && (
          <Box sx={{ flexShrink: 0 }}>
            {actions}
          </Box>
        )}
      </Stack>
    </Box>
  )
}

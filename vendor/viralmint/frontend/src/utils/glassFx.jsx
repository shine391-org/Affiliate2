/**
 * Apple-style glass + glow primitives shared across pages.
 *
 * One source of truth for the frosted-glass panel sx, the selection-pulse
 * keyframes, the CTA shimmer sweep, and the ambient aurora that drifts in
 * the page background. Importing from here keeps every surface visually
 * consistent — change the values once, every page tracks.
 *
 * Usage:
 *   import { glassPanelSx, glassSelectedGlow, auroraBackgroundSx,
 *            pulseGlow, pulseGlowWarm, shimmerSweep } from "../utils/glassFx"
 *
 *   <Paper sx={(theme) => glassPanelSx(theme)} />
 *   <Box  sx={{ ...glassPanelSx(theme), ...glassSelectedGlow }} />
 *   <Box  sx={(theme) => auroraBackgroundSx(theme)} />
 */
import { keyframes } from "@mui/system"
import { forwardRef } from "react"
import { Paper, Box, IconButton } from "@mui/material"
import CheckCircleOutlinedIcon from "@mui/icons-material/CheckCircleOutlined"
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline"
import WarningAmberIcon from "@mui/icons-material/WarningAmber"
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined"
import CloseIcon from "@mui/icons-material/Close"

// ── Keyframes ──────────────────────────────────────────────────────────

/** Cool-tone (cyan/blue) selection pulse — used by Smart Video, etc. */
export const pulseGlow = keyframes`
  0%, 100% { box-shadow: 0 0 0 1px rgba(99,179,237,0.6),  0 0 18px rgba(99,179,237,0.35), inset 0 0 14px rgba(99,179,237,0.10); }
  50%      { box-shadow: 0 0 0 1px rgba(99,179,237,0.9),  0 0 32px rgba(99,179,237,0.55), inset 0 0 22px rgba(99,179,237,0.18); }
`

/** Warm-brand (terracotta) selection pulse — used by sidebar active nav. */
export const pulseGlowWarm = keyframes`
  0%, 100% { box-shadow: 0 0 0 1px rgba(201,100,66,0.50), 0 0 14px rgba(201,100,66,0.28), inset 0 0 10px rgba(201,100,66,0.08); }
  50%      { box-shadow: 0 0 0 1px rgba(201,100,66,0.85), 0 0 24px rgba(201,100,66,0.46), inset 0 0 18px rgba(201,100,66,0.14); }
`

/** Diagonal sheen sweep for primary CTAs (Generate, Submit, etc.). */
export const shimmerSweep = keyframes`
  0%   { transform: translateX(-100%); }
  60%  { transform: translateX(220%); }
  100% { transform: translateX(220%); }
`

/** Slow drifting aurora for page-background ambiance. */
export const auroraDrift = keyframes`
  0%   { transform: translate(0, 0) scale(1); }
  50%  { transform: translate(20px, -10px) scale(1.05); }
  100% { transform: translate(0, 0) scale(1); }
`

// ── SX helpers ─────────────────────────────────────────────────────────

/**
 * Frosted-glass panel — translucent gradient bg + backdrop-filter blur.
 * Returns an sx object; use as `sx={(theme) => glassPanelSx(theme)}` or
 * spread into an existing sx with the same theme arg.
 *
 * Pass `{ cheap: true }` to drop the `backdrop-filter` blur and keep
 * only the gradient + border + shadow. Use this for surfaces that
 * render in dense lists or filmstrips where many translucent panels
 * stack and the compositor cost of repeated blur becomes a scroll-jank
 * risk. Visually nearly identical against the page aurora; the trade is
 * "slightly less frosted under content that scrolls behind."
 */
export const glassPanelSx = (theme, opts = {}) => {
  const { cheap = false } = opts
  return {
    position: "relative",
    borderRadius: 3,
    // Light-mode fill is a WARM off-white (not pure white) so panels read
    // as part of the linen-cream page rather than cold panes floating on
    // top of it. The inset white sheen below keeps the glass "lit edge".
    background: theme.palette.mode === "dark"
      ? "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02))"
      : "linear-gradient(135deg, rgba(255,251,245,0.82), rgba(250,243,234,0.5))",
    ...(cheap ? {} : {
      backdropFilter: "blur(20px) saturate(180%)",
      WebkitBackdropFilter: "blur(20px) saturate(180%)",
    }),
    border: "1px solid",
    borderColor: theme.palette.mode === "dark"
      ? "rgba(255,255,255,0.08)"
      : "rgba(255,255,255,0.7)",
    boxShadow: theme.palette.mode === "dark"
      ? "0 8px 32px rgba(0,0,0,0.32), inset 0 1px 0 rgba(255,255,255,0.06)"
      : "0 8px 28px rgba(120,95,72,0.10), inset 0 1px 0 rgba(255,255,255,0.85)",
  }
}

/** Cyan/blue selection pulse — spread into the sx of a selected card/row. */
export const glassSelectedGlow = {
  animation: `${pulseGlow} 2.6s ease-in-out infinite`,
}

/**
 * Variant of glassPanelSx tuned for clickable grid cards — slightly tighter
 * radius, plus an on-hover lift + soft cyan halo so the same surface used
 * for a static panel reads as actionable when wrapped around a button/link.
 *
 * Pass `selected: true` to add the cyan pulse glow; pass `hover: false` to
 * suppress the lift (e.g. a card that opens a modal but isn't list-y).
 */
export const glassCardSx = (theme, opts = {}) => {
  const { hover = true, selected = false, cheap = false } = opts
  return {
    ...glassPanelSx(theme, { cheap }),
    borderRadius: 2.5,
    transition: "transform .18s ease, box-shadow .18s ease, border-color .18s ease",
    ...(hover && {
      "&:hover": {
        transform: "translateY(-2px)",
        borderColor: theme.palette.mode === "dark"
          ? "rgba(99,179,237,0.35)"
          : "rgba(99,179,237,0.45)",
        boxShadow: theme.palette.mode === "dark"
          ? "0 12px 36px rgba(0,0,0,0.42), 0 0 28px rgba(99,179,237,0.20), inset 0 1px 0 rgba(255,255,255,0.08)"
          : "0 12px 36px rgba(120,95,72,0.14), 0 0 28px rgba(99,179,237,0.18), inset 0 1px 0 rgba(255,255,255,0.9)",
      },
    }),
    ...(selected && glassSelectedGlow),
  }
}

/** Brand-warm selection pulse — for terracotta accents (sidebar active nav). */
export const glassSelectedGlowWarm = {
  animation: `${pulseGlowWarm} 2.8s ease-in-out infinite`,
}

/**
 * Drop-in `<Paper>` replacement that paints the frosted-glass treatment.
 *
 * Use this for static panels (settings cards, info boxes, side rails).
 * For grid cards that the user clicks, use `<GlassCard>` instead — it adds
 * hover lift + selected-glow on top.
 *
 * Any sx passed in is spread AFTER the glass sx so callers can still
 * override padding, custom borderRadius, etc.
 */
export const GlassPanel = forwardRef(function GlassPanel({ sx, cheap = false, ...rest }, ref) {
  return (
    <Paper
      ref={ref}
      elevation={0}
      {...rest}
      sx={(theme) => ({
        ...glassPanelSx(theme, { cheap }),
        ...(typeof sx === "function" ? sx(theme) : sx),
      })}
    />
  )
})

/**
 * Drop-in `<Paper>` replacement for clickable / grid cards.
 *
 * Props:
 *   - selected: boolean — adds the cyan pulse glow when true
 *   - hover:    boolean — adds the lift + halo on hover (default true)
 *   - cheap:    boolean — drops the backdrop-filter blur for list-heavy
 *               surfaces (Channels video grid, Videos rows, filmstrips).
 *               See glassPanelSx for the rationale.
 */
export const GlassCard = forwardRef(function GlassCard(
  { selected = false, hover = true, cheap = false, sx, ...rest },
  ref,
) {
  return (
    <Paper
      ref={ref}
      elevation={0}
      {...rest}
      sx={(theme) => ({
        ...glassCardSx(theme, { hover, selected, cheap }),
        ...(typeof sx === "function" ? sx(theme) : sx),
      })}
    />
  )
})

/**
 * Toast / inline alert with the glass treatment.
 *
 * Replaces MUI's `<Alert variant="filled">` (solid colored bg, harsh) with
 * a frosted-glass surface + a 3px severity-colored left edge + a colored
 * severity icon. Reads as "this is a {severity} message" without dousing
 * the whole toast in solid color, matching the Apple-Notification-Center
 * vibe of the rest of the app.
 *
 * Used as the single source for the global `<GlobalSnackbar>` toast and
 * available for inline severity callouts that want the same treatment.
 *
 * Props:
 *   severity: "success" | "error" | "warning" | "info" (default "info")
 *   onClose:  optional close handler — renders ✕ on the right
 *   action:   optional ReactNode rendered right of the message
 *             (typically a Button — e.g. "Install" deep-link from snackbar)
 */
const SEVERITY_ICONS = {
  success: CheckCircleOutlinedIcon,
  error: ErrorOutlineIcon,
  warning: WarningAmberIcon,
  info: InfoOutlinedIcon,
}

export const GlassAlert = forwardRef(function GlassAlert(
  { severity = "info", onClose, action, children, sx, ...rest },
  ref,
) {
  const Icon = SEVERITY_ICONS[severity] || InfoOutlinedIcon
  return (
    <Paper
      ref={ref}
      elevation={0}
      role="alert"
      {...rest}
      sx={(theme) => ({
        ...glassPanelSx(theme),
        borderRadius: 2.5,
        borderLeft: `3px solid ${theme.palette[severity]?.main || theme.palette.info.main}`,
        display: "flex",
        alignItems: "center",
        gap: 1.25,
        px: 1.75, py: 1.25,
        minWidth: 320,
        maxWidth: 560,
        ...(typeof sx === "function" ? sx(theme) : sx),
      })}
    >
      <Icon sx={{ color: `${severity}.main`, fontSize: 22, flexShrink: 0 }} />
      <Box sx={{ flex: 1, minWidth: 0, fontSize: "0.88rem", lineHeight: 1.5 }}>
        {children}
      </Box>
      {action}
      {onClose && (
        <IconButton
          size="small"
          onClick={onClose}
          aria-label="Close"
          sx={{ ml: 0.25, color: "text.secondary", "&:hover": { color: "text.primary" } }}
        >
          <CloseIcon sx={{ fontSize: 18 }} />
        </IconButton>
      )}
    </Paper>
  )
})

/**
 * Ambient aurora — two big soft gradient blobs drifting in the background.
 *
 * Apply on a positioned parent (relative/absolute) with overflow:hidden.
 * Uses ::before / ::after, so do NOT use this on an element that already
 * relies on those pseudo-elements.
 *
 * Defaults to the brand palette (warm terracotta + mint). Override for
 * page-specific accents by passing { a, b } colors.
 */
export const auroraBackgroundSx = (theme, opts = {}) => {
  const a = opts.a || "#C96442"   // warm terracotta (brand)
  const b = opts.b || "#0D9F6E"   // mint green (brand)
  return {
    "&::before, &::after": {
      content: '""',
      position: "absolute",
      width: 520, height: 520,
      borderRadius: "50%",
      filter: "blur(80px)",
      opacity: theme.palette.mode === "dark" ? 0.18 : 0.12,
      animation: `${auroraDrift} 14s ease-in-out infinite`,
      pointerEvents: "none",
      zIndex: 0,
    },
    "&::before": {
      top: -200, left: -120,
      background: `radial-gradient(circle, ${a}, transparent 70%)`,
    },
    "&::after": {
      bottom: -240, right: -180,
      background: `radial-gradient(circle, ${b}, transparent 70%)`,
      animationDelay: "-7s",
    },
  }
}

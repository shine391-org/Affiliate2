import { useState, useRef, useEffect } from "react"
import {
  Box, Typography, ToggleButton, ToggleButtonGroup, Slider, Button,
  Stack, IconButton,
} from "@mui/material"
import { GlassPanel } from "../../utils/glassFx"
import BrandingWatermarkOutlinedIcon from "@mui/icons-material/BrandingWatermarkOutlined"
import UploadFileOutlinedIcon from "@mui/icons-material/UploadFileOutlined"
import CloseIcon from "@mui/icons-material/Close"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

const POSITIONS = [
  { value: "top-left", label: "Top Left" },
  { value: "top-right", label: "Top Right" },
  { value: "bottom-left", label: "Bottom Left" },
  { value: "bottom-right", label: "Bottom Right" },
]

export default function ToolWatermark() {
  useDocumentTitle("Add Watermark")
  const [position, setPosition] = useState("bottom-right")
  const [opacity, setOpacity] = useState(0.8)
  const [sizePct, setSizePct] = useState(8)
  const [logo, setLogo] = useState(null)
  const [logoUrl, setLogoUrl] = useState(null)
  const logoInputRef = useRef(null)

  // Create one blob URL per logo and revoke it when swapped/unmounted.
  // URL.createObjectURL in render would leak on every re-render.
  useEffect(() => {
    if (!logo) { setLogoUrl(null); return }
    const url = URL.createObjectURL(logo)
    setLogoUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [logo])

  return (
    <ToolRunner
      title="Add Watermark"
      description="Brand every export with your logo"
      icon={<BrandingWatermarkOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/watermark"
      extraFiles={{ logo }}
      canSubmit={!!logo}
      disabledReason="Upload a logo first"
      fieldBuilder={() => ({ position, opacity, size_pct: sizePct })}
    >
      <Stack spacing={2.5}>
        {/* Logo upload */}
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>
            Logo (PNG with transparency recommended)
          </Typography>
          <input
            ref={logoInputRef}
            type="file"
            hidden
            accept=".png,.jpg,.jpeg"
            onChange={(e) => setLogo(e.target.files?.[0] || null)}
          />
          {logo ? (
            <GlassPanel sx={{ p: 1.5, display: "flex", alignItems: "center", gap: 1.5 }}>
              <Box
                component="img"
                src={logoUrl}
                alt=""
                sx={{ width: 40, height: 40, objectFit: "contain", bgcolor: "action.hover", borderRadius: 0.5 }}
              />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>{logo.name}</Typography>
                <Typography variant="caption" sx={{ color: "text.secondary" }}>
                  {(logo.size / 1024).toFixed(0)} KB
                </Typography>
              </Box>
              <IconButton size="small" onClick={() => setLogo(null)}><CloseIcon fontSize="small" /></IconButton>
            </GlassPanel>
          ) : (
            <Button
              variant="outlined"
              startIcon={<UploadFileOutlinedIcon />}
              onClick={() => logoInputRef.current?.click()}
              sx={{ textTransform: "none" }}
            >
              Choose logo
            </Button>
          )}
        </Box>

        {/* Position */}
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>
            Position
          </Typography>
          <ToggleButtonGroup
            value={position}
            exclusive
            onChange={(_, v) => v && setPosition(v)}
            size="small"
          >
            {POSITIONS.map((p) => (
              <ToggleButton key={p.value} value={p.value} sx={{ textTransform: "none" }}>
                {p.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Box>

        {/* Opacity */}
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block" }}>
            Opacity: {Math.round(opacity * 100)}%
          </Typography>
          <Slider
            value={opacity} onChange={(_, v) => setOpacity(v)}
            min={0.2} max={1} step={0.05}
            size="small"
          />
        </Box>

        {/* Size */}
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block" }}>
            Size: {sizePct}% of video height
          </Typography>
          <Slider
            value={sizePct} onChange={(_, v) => setSizePct(v)}
            min={4} max={20} step={1}
            size="small"
          />
        </Box>
      </Stack>
    </ToolRunner>
  )
}

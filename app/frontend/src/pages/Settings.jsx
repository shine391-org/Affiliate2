import {
  Box, Typography, Stack, Card, CardContent,
  Button, Tab, Tabs, alpha, useTheme,
} from "@mui/material"
import MonitorHeartIcon from "@mui/icons-material/MonitorHeartOutlined"
import KeyIcon from "@mui/icons-material/VpnKeyOutlined"
import SmartToyIcon from "@mui/icons-material/SmartToyOutlined"
import MovieFilterIcon from "@mui/icons-material/MovieFilterOutlined"
import RecordVoiceOverOutlinedIcon from "@mui/icons-material/RecordVoiceOverOutlined"
import HealthDashboard from "../components/settings/HealthDashboard"
import MotionGraphicsSection from "../components/settings/MotionGraphicsSection"
import AIProviderSection from "../components/settings/AIProviderSection"
import TTSProviderSection from "../components/settings/TTSProviderSection"
import LocalizationProfilesSection from "../components/settings/LocalizationProfilesSection"
import ServiceKeysSection from "../components/settings/ServiceKeysSection"
import useSettings from "../hooks/useSettings"
import useDocumentTitle from "../hooks/useDocumentTitle"
import { useSearchParams } from "react-router-dom"
import { SETTINGS_SECTIONS, normalizeSettingsSection } from "../settingsModel"

function Section({ icon, title, description, children, accentColor }) {
  const theme = useTheme()
  const color = accentColor || theme.palette.primary.main
  return (
    <Card sx={{
      overflow: "visible",
      "&:hover": { borderColor: alpha(color, 0.25) },
    }}>
      <CardContent sx={{ p: 0, "&:last-child": { pb: 0 } }}>
        {title && (
          <Box sx={{
            px: 3, pt: 2.5, pb: 2,
            background: `linear-gradient(135deg, ${alpha(color, 0.06)}, ${alpha(color, 0.02)})`,
            borderBottom: 1, borderColor: "divider",
          }}>
            <Stack direction="row" spacing={1.5} alignItems="center">
              {icon && (
                <Box sx={{
                  width: 36, height: 36, borderRadius: 2,
                  bgcolor: alpha(color, 0.1),
                  display: "flex", alignItems: "center", justifyContent: "center",
                  color: color,
                }}>
                  {icon}
                </Box>
              )}
              <Box>
                <Typography variant="h6" sx={{ fontWeight: 700, fontSize: "1rem", lineHeight: 1.3 }}>{title}</Typography>
                {description && (
                  <Typography variant="body2" sx={{ color: "text.secondary", fontSize: "0.8rem", mt: 0.25 }}>
                    {description}
                  </Typography>
                )}
              </Box>
            </Stack>
          </Box>
        )}
        <Box sx={{ p: 3 }}>
          {children}
        </Box>
      </CardContent>
    </Card>
  )
}


export default function Settings() {
  useDocumentTitle("Settings")
  const { settings, loading, error, fetchSettings, updateSettings } = useSettings()
  const [params, setParams] = useSearchParams()
  const section = normalizeSettingsSection(params.get("section"))

  const setSection = (_, value) => {
    setParams((previous) => {
      const next = new URLSearchParams(previous)
      next.set("section", value)
      return next
    }, { replace: true })
  }

  if (loading) return (
    <Box sx={{ p: 4, display: "flex", justifyContent: "center" }}>
      <Typography sx={{ color: "text.secondary" }}>Đang tải cài đặt...</Typography>
    </Box>
  )
  if (error || !settings) return (
    <Box sx={{ p: 4, textAlign: "center" }}>
      <Typography sx={{ color: "error.main", mb: 2 }}>{error || "Không thể tải cài đặt"}</Typography>
      <Button variant="outlined" onClick={fetchSettings}>Thử lại</Button>
    </Box>
  )

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* ── Header ── */}
      <Box sx={{
        px: 3, py: 2, flexShrink: 0,
        borderBottom: 1, borderColor: "divider",
        background: (t) => t.palette.mode === "dark"
          ? "linear-gradient(135deg, rgba(107,114,128,0.10) 0%, rgba(30,28,26,1) 100%)"
          : "linear-gradient(135deg, rgba(107,114,128,0.07) 0%, rgba(255,255,255,1) 100%)",
      }}>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <KeyIcon sx={{ color: "text.secondary", fontSize: 26 }} />
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 700, letterSpacing: -0.3 }}>
              Settings
            </Typography>
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              Connections, reusable profiles, voices, service keys and system health
            </Typography>
          </Box>
        </Stack>
      </Box>

      <Box sx={{ px: { xs: 1, md: 3 }, borderBottom: 1, borderColor: "divider", bgcolor: "background.paper", flexShrink: 0 }}>
        <Tabs
          value={section}
          onChange={setSection}
          variant="scrollable"
          scrollButtons="auto"
          aria-label="Các mục cài đặt"
        >
          {SETTINGS_SECTIONS.map((item) => <Tab key={item.value} value={item.value} label={item.label} />)}
        </Tabs>
      </Box>

      {/* One configuration owner at a time — no endless card wall. */}
      <Box sx={{ flex: 1, overflow: "auto", p: { xs: 2, md: 3 } }}>
        <Box sx={{ maxWidth: 920, mx: "auto" }}>
          {section === "ai" && (
            <Section
              icon={<SmartToyIcon sx={{ fontSize: 20 }} />}
              title="Kết nối AI"
              description="Nhà cung cấp, endpoint, thông tin xác thực và mô hình mặc định/toàn cục"
              accentColor="#0d9f6e"
            >
              <AIProviderSection settings={settings} updateSettings={updateSettings} />
            </Section>
          )}

          {section === "localization" && (
            <Section
              icon={<RecordVoiceOverOutlinedIcon sx={{ fontSize: 20 }} />}
              title="Hồ sơ bản địa hóa"
              description="Cấu hình dùng lại cho mô hình C6/C7, phong cách ngôn ngữ, ASR, nhịp, âm thanh, kết xuất và QC"
              accentColor="#0f766e"
            >
              <LocalizationProfilesSection settings={settings} />
            </Section>
          )}

          {section === "tts" && (
            <Section
              icon={<RecordVoiceOverOutlinedIcon sx={{ fontSize: 20 }} />}
              title="TTS / Giọng đọc"
              description="Kết nối và thông tin xác thực TTS dùng chung. Hồ sơ tham chiếu các mặc định này mà không sao chép bí mật."
              accentColor="#9333ea"
            >
              <TTSProviderSection settings={settings} updateSettings={updateSettings} />
            </Section>
          )}

          {section === "services" && (
            <Section
              icon={<KeyIcon sx={{ fontSize: 20 }} />}
              title="Nguồn dữ liệu / Khóa dịch vụ"
              description="Thông tin xác thực theo từng dịch vụ dùng cho khám phá và các tích hợp được hỗ trợ"
              accentColor="#2563eb"
            >
              <ServiceKeysSection settings={settings} updateSettings={updateSettings} />
            </Section>
          )}

          {section === "addons" && (
            <Section
              icon={<MovieFilterIcon sx={{ fontSize: 20 }} />}
              title="Tiện ích bổ sung"
              description="Các engine tùy chọn được cài khi cần vào thư mục dữ liệu"
              accentColor="#7c3aed"
            >
              <MotionGraphicsSection />
            </Section>
          )}

          {section === "health" && (
            <Section
              icon={<MonitorHeartIcon sx={{ fontSize: 20 }} />}
              title="Tình trạng hệ thống"
              description="Trạng thái phụ thuộc, khả dụng nhà cung cấp và kiểm tra lưu trữ/runtime"
              accentColor="#dc2626"
            >
              <HealthDashboard />
            </Section>
          )}
        </Box>
      </Box>
    </Box>
  )
}

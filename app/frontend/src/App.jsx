import { lazy, Suspense } from "react"
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from "react-router-dom"
import { Snackbar, Alert, Button, CircularProgress, Box } from "@mui/material"
import ErrorBoundary from "./components/ErrorBoundary"
import Layout from "./components/Layout"
import useAppStore from "./store/appStore"
import { legacyRedirectHref } from "./navigation"

// Route-level code splitting — Assistant remains available, but Discover owns home.
import Chat from "./pages/Chat"
const Settings = lazy(() => import("./pages/Settings"))
const Library = lazy(() => import("./pages/Library"))
const Discover = lazy(() => import("./pages/Discover"))
const Candidates = lazy(() => import("./pages/Candidates"))
const Scout = lazy(() => import("./pages/Scout"))
const StockVideo = lazy(() => import("./pages/StockVideo"))
const Channels = lazy(() => import("./pages/Channels"))
const Messaging = lazy(() => import("./pages/Messaging"))
const Studio = lazy(() => import("./pages/Studio"))
const MotionGraphics = lazy(() => import("./pages/MotionGraphics"))
const Tools = lazy(() => import("./pages/Tools"))
const LocalizationBatch = lazy(() => import("./pages/LocalizationBatch"))
const LocalizationReview = lazy(() => import("./pages/LocalizationReview"))
const Publish = lazy(() => import("./pages/Publish"))
const ReviewQueue = lazy(() => import("./plugins/review/ReviewQueue"))
const Performance = lazy(() => import("./plugins/performance/Performance"))

// Utilities — single-purpose pages remain thin wrappers around ToolRunner.
const ToolCaptions = lazy(() => import("./pages/tools/Captions"))
const ToolReframe = lazy(() => import("./pages/tools/Reframe"))
const ToolAudioEnhance = lazy(() => import("./pages/tools/AudioEnhance"))
const ToolWatermark = lazy(() => import("./pages/tools/Watermark"))
const ToolRemoveSilence = lazy(() => import("./pages/tools/RemoveSilence"))
const ToolMergeClips = lazy(() => import("./pages/tools/MergeClips"))
const ToolGif = lazy(() => import("./pages/tools/Gif"))
const ToolSpeed = lazy(() => import("./pages/tools/Speed"))
const ToolTrim = lazy(() => import("./pages/tools/Trim"))
const ToolSubtitles = lazy(() => import("./pages/tools/Subtitles"))
const ToolAutoZoom = lazy(() => import("./pages/tools/AutoZoom"))
const ToolTransform = lazy(() => import("./pages/tools/Transform"))
const ToolMusicVisualizer = lazy(() => import("./pages/tools/MusicVisualizer"))
const ToolVoiceover = lazy(() => import("./pages/tools/Voiceover"))
const ToolHookAnalysis = lazy(() => import("./pages/tools/HookAnalysis"))
const ToolTranslate = lazy(() => import("./pages/tools/Translate"))
const ToolMetadata = lazy(() => import("./pages/tools/Metadata"))
const ToolAutoChapters = lazy(() => import("./pages/tools/AutoChapters"))
const ToolCompress = lazy(() => import("./pages/tools/Compress"))
const ToolCrop = lazy(() => import("./pages/tools/Crop"))
const ToolVideoDownload = lazy(() => import("./pages/tools/VideoDownload"))

const LazyFallback = () => (
  <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100%", minHeight: 200 }}>
    <CircularProgress size={36} />
  </Box>
)

function LegacyRedirect({ to, suffix = "" }) {
  const location = useLocation()
  return <Navigate to={legacyRedirectHref(`${to}${suffix}`, location.search, location.hash)} replace />
}

function LegacyToolsRedirect() {
  const location = useLocation()
  const suffix = location.pathname.slice("/tools".length)
  return <LegacyRedirect to="/utilities" suffix={suffix} />
}

function GlobalSnackbar() {
  const snackbar = useAppStore((s) => s.snackbar)
  const closeSnackbar = useAppStore((s) => s.closeSnackbar)
  const navigate = useNavigate()

  return (
    <Snackbar
      open={snackbar.open}
      autoHideDuration={snackbar.action ? 6000 : 4000}
      onClose={closeSnackbar}
      anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
    >
      <Alert
        onClose={closeSnackbar}
        severity={snackbar.severity}
        variant="filled"
        sx={{ width: "100%", borderRadius: 2, alignItems: "center" }}
        action={snackbar.action ? (
          <Button
            color="inherit"
            size="small"
            sx={{ fontWeight: 700, whiteSpace: "nowrap" }}
            onClick={() => { navigate(snackbar.action.href); closeSnackbar() }}
          >
            {snackbar.action.label}
          </Button>
        ) : undefined}
      >
        {snackbar.message}
      </Alert>
    </Snackbar>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <ErrorBoundary>
        <Suspense fallback={<LazyFallback />}>
          <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<Navigate to="/discover/spy" replace />} />
              <Route path="assistant" element={<Chat />} />
              <Route path="dashboard" element={<Navigate to="/library" replace />} />

              <Route path="discover" element={<Discover />}>
                <Route index element={<Navigate to="spy" replace />} />
                <Route path="spy" element={<Scout />} />
                <Route path="channels" element={<Channels />} />
                <Route path="candidates" element={<Candidates />} />
              </Route>

              <Route path="library" element={<Library />} />
              <Route path="studio" element={<Studio />} />
              <Route path="studio/localize" element={<Studio />} />
              <Route path="studio/manual" element={<Studio />} />
              <Route path="studio/smart-cut" element={<Studio />} />
              <Route path="studio/remake" element={<Studio />} />
              <Route path="review" element={<LocalizationReview />} />
              <Route path="publish" element={<Publish />} />
              <Route path="performance" element={<Performance />} />
              <Route path="utilities" element={<Tools />} />
              <Route path="settings" element={<Settings />} />

              {/* Secondary/transitional surfaces remain reachable without
                  competing for primary workflow ownership. */}
              <Route path="stock" element={<StockVideo />} />
              <Route path="ai-video" element={<Navigate to="/stock" replace />} />
              <Route path="avatar" element={<Navigate to="/stock" replace />} />
              <Route path="create" element={<Navigate to="/stock" replace />} />
              <Route path="cron" element={<Navigate to="/assistant" replace />} />
              <Route path="motion" element={<MotionGraphics />} />
              <Route path="messaging" element={<Messaging />} />
              <Route path="review-queue" element={<ReviewQueue />} />

              <Route path="utilities/localization-batch" element={<LegacyRedirect to="/studio/localize" suffix="?batch=1" />} />
              <Route path="utilities/localization-review" element={<LegacyRedirect to="/review" />} />
              <Route path="utilities/captions" element={<ToolCaptions />} />
              <Route path="utilities/reframe" element={<ToolReframe />} />
              <Route path="utilities/audio-enhance" element={<ToolAudioEnhance />} />
              <Route path="utilities/watermark" element={<ToolWatermark />} />
              <Route path="utilities/remove-silence" element={<ToolRemoveSilence />} />
              <Route path="utilities/merge-clips" element={<ToolMergeClips />} />
              <Route path="utilities/gif" element={<ToolGif />} />
              <Route path="utilities/speed" element={<ToolSpeed />} />
              <Route path="utilities/trim" element={<ToolTrim />} />
              <Route path="utilities/subtitles" element={<ToolSubtitles />} />
              <Route path="utilities/auto-zoom" element={<ToolAutoZoom />} />
              <Route path="utilities/transform" element={<ToolTransform />} />
              <Route path="utilities/compress" element={<ToolCompress />} />
              <Route path="utilities/crop" element={<ToolCrop />} />
              <Route path="utilities/video-download" element={<ToolVideoDownload />} />
              <Route path="utilities/music-visualizer" element={<ToolMusicVisualizer />} />
              <Route path="utilities/voiceover" element={<ToolVoiceover />} />
              <Route path="utilities/hook-analysis" element={<ToolHookAnalysis />} />
              <Route path="utilities/translate" element={<LegacyRedirect to="/studio/localize" />} />
              <Route path="utilities/metadata" element={<ToolMetadata />} />
              <Route path="utilities/auto-chapters" element={<ToolAutoChapters />} />

              {/* Bookmark compatibility. Redirects preserve query/hash. */}
              <Route path="scout" element={<LegacyRedirect to="/discover/spy" />} />
              <Route path="channels" element={<LegacyRedirect to="/discover/channels" />} />
              <Route path="clips" element={<LegacyRedirect to="/studio/manual" />} />
              <Route path="videos" element={<LegacyRedirect to="/library" />} />
              <Route path="tools/translate" element={<LegacyRedirect to="/studio/localize" />} />
              <Route path="tools/localization-batch" element={<LegacyRedirect to="/studio/localize" suffix="?batch=1" />} />
              <Route path="tools/localization-review" element={<LegacyRedirect to="/review" />} />
              <Route path="tools/*" element={<LegacyToolsRedirect />} />

              <Route path="*" element={<Navigate to="/discover/spy" replace />} />
            </Route>
          </Routes>
        </Suspense>
        <GlobalSnackbar />
      </ErrorBoundary>
    </BrowserRouter>
  )
}

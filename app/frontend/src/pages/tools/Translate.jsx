import { useEffect, useMemo, useState } from "react"
import {
  Alert, Box, FormControl, FormControlLabel, InputLabel, MenuItem,
  Radio, RadioGroup, Select, Stack, TextField, Typography,
} from "@mui/material"
import TranslateOutlinedIcon from "@mui/icons-material/TranslateOutlined"
import http from "../../api/http"
import { CAPTION_STYLES } from "../../components/tools/captionOptions"
import LocalizationExpertPanel from "../../components/tools/LocalizationExpertPanel"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

const LANGUAGES = [
  { code: "vi", name: "Vietnamese" },
  { code: "en", name: "English" },
  { code: "es", name: "Spanish" },
  { code: "fr", name: "French" },
  { code: "de", name: "German" },
  { code: "it", name: "Italian" },
  { code: "pt", name: "Portuguese" },
  { code: "nl", name: "Dutch" },
  { code: "pl", name: "Polish" },
  { code: "ru", name: "Russian" },
  { code: "ja", name: "Japanese" },
  { code: "ko", name: "Korean" },
  { code: "zh", name: "Chinese (Mandarin)" },
  { code: "ar", name: "Arabic" },
  { code: "hi", name: "Hindi" },
  { code: "tr", name: "Turkish" },
  { code: "sv", name: "Swedish" },
  { code: "da", name: "Danish" },
  { code: "no", name: "Norwegian" },
  { code: "fi", name: "Finnish" },
]

export default function ToolTranslate() {
  useDocumentTitle("Translate + Dub")
  const [target, setTarget] = useState("vi")
  const [mode, setMode] = useState("full_dub")
  const [voiceId, setVoiceId] = useState("")
  const [captionStyle, setCaptionStyle] = useState("viral")
  const [emojiStyle, setEmojiStyle] = useState("moderate")
  const [profileId, setProfileId] = useState("")
  const [profiles, setProfiles] = useState([])
  const [profileDefault, setProfileDefault] = useState("")

  useEffect(() => {
    let alive = true
    http.get("/api/settings/localization-profiles")
      .then(({ data }) => {
        if (!alive) return
        setProfiles(data?.profiles || [])
        setProfileDefault(data?.default_profile_id || "")
        setProfileId(data?.default_profile_id || "")
      })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  const selectedProfile = useMemo(
    () => profiles.find((p) => p.id === profileId),
    [profiles, profileId],
  )

  const fields = () => ({
    target_language: target,
    mode,
    caption_style: captionStyle,
    emoji_style: emojiStyle,
    profile_id: profileId || profileDefault || undefined,
    voice_id: mode === "full_dub" && voiceId.trim() ? voiceId.trim() : undefined,
  })

  const targetName = LANGUAGES.find((l) => l.code === target)?.name || target

  return (
    <ToolRunner
      title="Translate + Dub"
      description="Donor-derived semantic translation, smart dubbing, clean background audio and HQ render"
      icon={<TranslateOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/translate"
      fieldBuilder={fields}
      processLabel={mode === "full_dub" ? "HQ translate + dub to " + targetName : "Translate captions to " + targetName}
      resultPreview={(jobId) => <LocalizationExpertPanel jobId={jobId} />}
    >
      <Stack spacing={2.5}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <FormControl size="small" fullWidth>
            <InputLabel>Target language</InputLabel>
            <Select value={target} label="Target language" onChange={(e) => setTarget(e.target.value)}>
              {LANGUAGES.map((l) => <MenuItem key={l.code} value={l.code}>{l.name}</MenuItem>)}
            </Select>
          </FormControl>

          <FormControl size="small" fullWidth>
            <InputLabel>Localization profile</InputLabel>
            <Select value={profileId} label="Localization profile" onChange={(e) => setProfileId(e.target.value)}>
              {profiles.map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
            </Select>
          </FormControl>
        </Stack>

        {selectedProfile && (
          <Alert severity="info" variant="outlined">
            <Typography variant="body2" sx={{ fontWeight: 600 }}>{selectedProfile.name}</Typography>
            <Typography variant="caption" color="text.secondary">
              ASR {selectedProfile.asr_quality}
              {" · alignment " + (selectedProfile.alignment_mode || "off")}
              {" · diarization " + (selectedProfile.diarization_mode || "off")}
              {" · audio " + selectedProfile.audio_mode}
              {" · render " + selectedProfile.render_profile}
              {" · speed ceiling " + Number(selectedProfile.dub_speed_max || 1.3).toFixed(2) + "×"}
            </Typography>
          </Alert>
        )}

        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.75 }}>
            Localization mode
          </Typography>
          <RadioGroup value={mode} onChange={(e) => setMode(e.target.value)}>
            <FormControlLabel
              value="full_dub"
              control={<Radio size="small" />}
              label={
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>HQ full dub</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Clean vocal/background separation, semantic translation, measured TTS fitting and final-timing captions.
                  </Typography>
                </Box>
              }
            />
            <FormControlLabel
              value="captions_only"
              control={<Radio size="small" />}
              label={
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>Translated captions only</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Keep original audio; use the same semantic translation pipeline for captions.
                  </Typography>
                </Box>
              }
            />
          </RadioGroup>
        </Box>

        {mode === "full_dub" && (
          <TextField
            label="Voice override (optional)"
            size="small"
            value={voiceId}
            onChange={(e) => setVoiceId(e.target.value)}
            placeholder="Leave empty to use profile / speaker voice mapping"
            helperText="Works with the provider configured in Settings. Expert mode can override individual segments after rendering."
          />
        )}

        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <FormControl size="small" fullWidth>
            <InputLabel>Caption style</InputLabel>
            <Select value={captionStyle} label="Caption style" onChange={(e) => setCaptionStyle(e.target.value)}>
              {CAPTION_STYLES.map((s) => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl size="small" fullWidth>
            <InputLabel>Emoji intensity</InputLabel>
            <Select value={emojiStyle} label="Emoji intensity" onChange={(e) => setEmojiStyle(e.target.value)}>
              <MenuItem value="none">None</MenuItem>
              <MenuItem value="minimal">Minimal</MenuItem>
              <MenuItem value="moderate">Moderate</MenuItem>
              <MenuItem value="heavy">Heavy</MenuItem>
            </Select>
          </FormControl>
        </Stack>

        <Alert severity="success" variant="outlined">
          HQ mode no longer drops a line because one Whisper cue is too short.
          Adjacent cues are planned into dubbing chunks; generated TTS is measured,
          rewritten when necessary, fitted after synthesis, and can use bounded
          video retiming only as a last resort. The result panel exposes QC and
          expert rerun controls without re-uploading the source.
        </Alert>
      </Stack>
    </ToolRunner>
  )
}

import { useEffect, useMemo, useState } from "react"
import {
  Alert, Box, Checkbox, FormControl, FormControlLabel, InputLabel, MenuItem,
  Radio, RadioGroup, Select, Stack, TextField, Typography,
} from "@mui/material"
import TranslateOutlinedIcon from "@mui/icons-material/TranslateOutlined"
import http from "../../api/http"
import { CAPTION_STYLES } from "../../components/tools/captionOptions"
import LocalizationExpertPanel from "../../components/tools/LocalizationExpertPanel"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

const LANGUAGES = [
  { code: "vi", name: "Tiếng Việt" },
  { code: "en", name: "Tiếng Anh" },
  { code: "es", name: "Tiếng Tây Ban Nha" },
  { code: "fr", name: "Tiếng Pháp" },
  { code: "de", name: "Tiếng Đức" },
  { code: "it", name: "Tiếng Ý" },
  { code: "pt", name: "Tiếng Bồ Đào Nha" },
  { code: "nl", name: "Tiếng Hà Lan" },
  { code: "pl", name: "Tiếng Ba Lan" },
  { code: "ru", name: "Tiếng Nga" },
  { code: "ja", name: "Tiếng Nhật" },
  { code: "ko", name: "Tiếng Hàn" },
  { code: "zh", name: "Tiếng Trung (Quan thoại)" },
  { code: "ar", name: "Tiếng Ả Rập" },
  { code: "hi", name: "Tiếng Hindi" },
  { code: "tr", name: "Tiếng Thổ Nhĩ Kỳ" },
  { code: "sv", name: "Tiếng Thụy Điển" },
  { code: "da", name: "Tiếng Đan Mạch" },
  { code: "no", name: "Tiếng Na Uy" },
  { code: "fi", name: "Tiếng Phần Lan" },
]

export default function ToolTranslate() {
  useDocumentTitle("Dịch + lồng tiếng")
  const [target, setTarget] = useState("vi")
  const [mode, setMode] = useState("full_dub")
  const [voiceId, setVoiceId] = useState("")
  const [captionStyle, setCaptionStyle] = useState("viral")
  const [emojiStyle, setEmojiStyle] = useState("moderate")
  const [profileId, setProfileId] = useState("")
  const [reviewTranslation, setReviewTranslation] = useState(true)
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
    review_translation: mode === "full_dub" ? reviewTranslation : false,
    voice_id: mode === "full_dub" && voiceId.trim() ? voiceId.trim() : undefined,
  })

  const targetName = LANGUAGES.find((l) => l.code === target)?.name || target

  return (
    <ToolRunner
      title="Dịch + lồng tiếng"
      description="Dịch ngữ nghĩa, lồng tiếng thông minh, âm nền sạch và kết xuất HQ"
      icon={<TranslateOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/translate"
      fieldBuilder={fields}
      processLabel={mode === "full_dub" ? "Dịch HQ + lồng tiếng sang " + targetName : "Dịch phụ đề sang " + targetName}
      resultPreview={(jobId) => <LocalizationExpertPanel jobId={jobId} />}
    >
      <Stack spacing={2.5}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <FormControl size="small" fullWidth>
            <InputLabel>Ngôn ngữ đích</InputLabel>
            <Select value={target} label="Ngôn ngữ đích" onChange={(e) => setTarget(e.target.value)}>
              {LANGUAGES.map((l) => <MenuItem key={l.code} value={l.code}>{l.name}</MenuItem>)}
            </Select>
          </FormControl>

          <FormControl size="small" fullWidth>
            <InputLabel>Hồ sơ bản địa hóa</InputLabel>
            <Select value={profileId} label="Hồ sơ bản địa hóa" onChange={(e) => setProfileId(e.target.value)}>
              {profiles.map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
            </Select>
          </FormControl>
        </Stack>

        {selectedProfile && (
          <Alert severity="info" variant="outlined">
            <Typography variant="body2" sx={{ fontWeight: 600 }}>{selectedProfile.name}</Typography>
            <Typography variant="caption" color="text.secondary">
              ASR {selectedProfile.asr_quality}
              {" · căn chỉnh " + (selectedProfile.alignment_mode || "off")}
              {" · tách người nói " + (selectedProfile.diarization_mode || "off")}
              {" · dịch " + (selectedProfile.translation_preset || "natural_vietnamese")}
              {" · mô hình C6 " + (selectedProfile.translation_model || "global")}
              {" · viết lại C7 " + (selectedProfile.rewrite_model || "global")}
              {" · âm thanh " + selectedProfile.audio_mode}
              {" · kết xuất " + selectedProfile.render_profile}
              {" · giới hạn tốc độ " + Number(selectedProfile.dub_speed_max || 1.3).toFixed(2) + "×"}
            </Typography>
          </Alert>
        )}

        {mode === "full_dub" && (
          <FormControlLabel
            control={
              <Checkbox
                checked={reviewTranslation}
                onChange={(e) => setReviewTranslation(e.target.checked)}
              />
            }
            label={
              <Box>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>Duyệt bản dịch trước khi lồng tiếng</Typography>
                <Typography variant="caption" color="text.secondary">
                  Tạm dừng sau C6 để so sánh nguồn ↔ đích, sửa dòng chưa tốt rồi tiếp tục cùng tác vụ từ C7 mà không chạy lại ASR.
                </Typography>
              </Box>
            }
          />
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
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>Lồng tiếng HQ đầy đủ</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Tách giọng/nền sạch, dịch ngữ nghĩa, căn TTS theo thời lượng đo được và phụ đề theo nhịp cuối.
                  </Typography>
                </Box>
              }
            />
            <FormControlLabel
              value="captions_only"
              control={<Radio size="small" />}
              label={
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>Chỉ phụ đề đã dịch</Typography>
                  <Typography variant="caption" color="text.secondary">
                    Giữ âm thanh gốc; dùng cùng pipeline dịch ngữ nghĩa cho phụ đề.
                  </Typography>
                </Box>
              }
            />
          </RadioGroup>
        </Box>

        {mode === "full_dub" && (
          <TextField
            label="Ghi đè giọng đọc (tùy chọn)"
            size="small"
            value={voiceId}
            onChange={(e) => setVoiceId(e.target.value)}
            placeholder="Để trống để dùng ánh xạ giọng theo hồ sơ / người nói"
            helperText="Dùng nhà cung cấp đã cấu hình trong Cài đặt. Chế độ nâng cao có thể ghi đè từng đoạn sau khi kết xuất."
          />
        )}

        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <FormControl size="small" fullWidth>
            <InputLabel>Kiểu phụ đề</InputLabel>
            <Select value={captionStyle} label="Kiểu phụ đề" onChange={(e) => setCaptionStyle(e.target.value)}>
              {CAPTION_STYLES.map((s) => <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl size="small" fullWidth>
            <InputLabel>Mức emoji</InputLabel>
            <Select value={emojiStyle} label="Mức emoji" onChange={(e) => setEmojiStyle(e.target.value)}>
              <MenuItem value="none">Không</MenuItem>
              <MenuItem value="minimal">Ít</MenuItem>
              <MenuItem value="moderate">Vừa</MenuItem>
              <MenuItem value="heavy">Nhiều</MenuItem>
            </Select>
          </FormControl>
        </Stack>

        <Alert severity="success" variant="outlined">
          Chế độ HQ không còn bỏ dòng chỉ vì một cue Whisper quá ngắn.
          Các cue liền nhau được gom thành đoạn lồng tiếng; TTS tạo ra được đo thời lượng,
          được viết lại khi cần, căn sau tổng hợp và có thể dùng điều chỉnh video giới hạn
          chỉ như phương án cuối. Bảng kết quả hiển thị QC và
          điều khiển chạy lại nâng cao mà không cần tải lại nguồn.
        </Alert>
      </Stack>
    </ToolRunner>
  )
}

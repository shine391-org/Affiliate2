import { Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Stack, Typography } from "@mui/material"
import {
  PROVIDER_BINDING_CURRENT_LABEL,
  PROVIDER_BINDING_CURRENT_UNAVAILABLE_LABEL,
  PROVIDER_BINDING_KEEP_LABEL,
  PROVIDER_BINDING_MISMATCH_LABEL,
  bindingRewriteRoutes,
  bindingTranslationRoutes,
  bindingTtsLabel,
  changedStagesLabel,
  currentProvidersAvailable,
  providerBinding,
  providerBindingFromStage,
} from "../../providerBinding"

const STAGE_RESUME_LABELS = { translation: "Bản dịch C6", dubbing: "Lồng tiếng" }

const sideSummary = (side) => {
  if (!side) return "không khả dụng"
  const translation = bindingTranslationRoutes(side)[0]
  const rewrite = bindingRewriteRoutes(side)[0]
  return [
    translation ? "C6 " + [translation.connection_id, translation.model].filter(Boolean).join(" / ") : null,
    rewrite ? "C7 " + [rewrite.connection_id, rewrite.model].filter(Boolean).join(" / ") : null,
    bindingTtsLabel(side) ? "TTS " + bindingTtsLabel(side) : null,
  ].filter(Boolean).join(" · ") || "chưa cấu hình"
}

/**
 * Compact drift chip. Renders only when the backend flags a mismatch, so the
 * normal (unchanged) job surface stays clean.
 */
export function ProviderBindingChip({ detail, ...props }) {
  const binding = providerBinding(detail)
  // The backend owns this verdict; the frontend never compares providers itself.
  if (!binding?.binding_mismatch) return null
  const stages = changedStagesLabel(binding)
  return (
    <Chip
      size="small"
      color="warning"
      variant="outlined"
      data-testid="provider-binding-mismatch"
      label={stages ? PROVIDER_BINDING_MISMATCH_LABEL + " · " + stages : PROVIDER_BINDING_MISMATCH_LABEL}
      {...props}
    />
  )
}

/**
 * Small confirmation modal shown when a rerun is attempted on a drifted job.
 * Default (primary) choice keeps the job's own snapshot and original requested
 * stage; the explicit alternative adopts the live Settings binding.
 */
export function ProviderBindingRerunDialog({
  open,
  binding,
  requestedStage,
  busy = false,
  onKeepSnapshot,
  onUseCurrent,
  onCancel,
}) {
  const stages = changedStagesLabel(binding)
  const resumeStage = requestedStage
  const rebindStage = providerBindingFromStage(binding)
  const canUseCurrent = currentProvidersAvailable(binding)
  const currentError = binding?.current_error

  return (
    <Dialog open={Boolean(open)} onClose={busy ? undefined : onCancel} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontWeight: 700 }}>{PROVIDER_BINDING_MISMATCH_LABEL}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          <Typography variant="body2">
            {stages ? "Cấu hình đã đổi ở: " + stages + ". " : ""}
            {resumeStage ? "Giữ cấu hình job sẽ chạy lại từ " + (STAGE_RESUME_LABELS[resumeStage] || resumeStage) + ". " : "Giữ cấu hình job sẽ thử lại đúng bước lỗi. "}
            {rebindStage ? "Dùng cấu hình hiện tại sẽ chạy lại từ " + STAGE_RESUME_LABELS[rebindStage] + "." : ""}
          </Typography>
          <Box>
            <Typography variant="caption" color="text.secondary">Cấu hình của job</Typography>
            <Typography variant="body2">{sideSummary(binding?.job)}</Typography>
          </Box>
          <Box>
            <Typography variant="caption" color="text.secondary">Cấu hình hiện tại</Typography>
            <Typography variant="body2">{sideSummary(binding?.current)}</Typography>
          </Box>
          <Typography variant="caption" color="text.secondary">
            {PROVIDER_BINDING_KEEP_LABEL + ": giữ nguyên ảnh chụp provider lúc tạo job và bước yêu cầu ban đầu."}
          </Typography>
          {!canUseCurrent && <Alert severity="warning">{PROVIDER_BINDING_CURRENT_UNAVAILABLE_LABEL}</Alert>}
          {currentError && <Typography variant="caption" color="error">{currentError}</Typography>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={busy}>Hủy</Button>
        <Button
          variant="outlined"
          color="warning"
          onClick={onUseCurrent}
          disabled={busy || !canUseCurrent}
          title={canUseCurrent ? undefined : PROVIDER_BINDING_CURRENT_UNAVAILABLE_LABEL}
        >
          {busy ? "Đang cập nhật…" : PROVIDER_BINDING_CURRENT_LABEL}
        </Button>
        {/* Default action: keep the job's own provider snapshot. */}
        <Button variant="contained" autoFocus onClick={onKeepSnapshot} disabled={busy}>
          {PROVIDER_BINDING_KEEP_LABEL}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

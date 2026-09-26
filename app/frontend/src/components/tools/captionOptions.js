// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * Shared caption + emoji enums for the tool pages and Clip Studio.
 *
 * The VALUES are the contract: they must match the keys of
 * backend/services/caption_service.py CAPTION_STYLES, minus `brainrot` (a look
 * applied by its own pipeline, not a general preset).
 * tests/test_caption_styles_parity.py fails if the two drift.
 *
 * They drifted once already: this list and the /api/tools/captions Literal
 * were both pinned to the original three, so the Captions tool offered 3 of
 * 10 styles — neon, karaoke, glow, urban, warm and mono were unreachable from
 * the tool built to apply them.
 *
 * Descriptions summarize the real render params (highlight colour, font,
 * words_per_group) rather than being decorative.
 */
export const CAPTION_STYLES = [
  { value: "viral", label: "Viral", description: "Tô vàng, cụm khoảng 6 từ" },
  { value: "classic", label: "Cổ điển", description: "Cả câu, không tô sáng" },
  { value: "bold", label: "Đậm", description: "Tô xanh, cụm khoảng 4 từ" },
  { value: "neon", label: "Neon", description: "Chữ hồng, tô vàng, khoảng 6 từ" },
  { value: "minimal", label: "Tối giản", description: "Arial đơn giản, dòng dài, không tô sáng" },
  { value: "karaoke", label: "Karaoke", description: "Chữ xám sáng dần từng từ" },
  { value: "glow", label: "Phát sáng", description: "Phát sáng cam nhẹ, cụm khoảng 6 từ" },
  { value: "urban", label: "Đô thị đậm", description: "Arial Black, nhịp mạnh khoảng 3 từ" },
  { value: "warm", label: "Ánh sáng ấm", description: "Georgia serif, tông kem ấm" },
  { value: "mono", label: "Đơn sắc", description: "Verdana, tô xám, nhẹ nhàng" },
]

export const EMOJI_LEVELS = [
  { value: "none", label: "Không" },
  { value: "minimal", label: "Ít" },
  { value: "moderate", label: "Vừa" },
  { value: "heavy", label: "Nhiều" },
]

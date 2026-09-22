# PROJECT_STATUS.md

## Trạng thái tổng thể: **PHASE 4 IN PROGRESS** 🚧

Phase 1/2/3.1/3.2/3.3 đã hoàn thành. Phase 3.4/3.5/4 đã được implement theo stacked branches để QA end-to-end một lượt trước khi merge tuần tự.

---

## Phase 3.1 — Candidate Content Intelligence (COMPLETE ✅)

- 10 candidates analyzed
- 10/10 transcripts
- 10/10 intelligence
- Gemini 3.5 Flash-Lite
- Human review generated
- Commit: `b9a0e02` / `075497a`

---

## Phase 3.2 — Controlled Vietnamese Remake Pilot (COMPLETE ✅)

3/3 candidate pilot đã render thành Vietnamese prototype để human review. Tất cả output 1080x1920, Vietnamese Edge TTS + Vietnamese burned subtitles. Commit `541d720`.

Pilot A: Douyin #7653466619146170234 (one-pot recipe, 26s)
Pilot B: Bilibili #117272756624085 (diet skit, ~29s)
Pilot C: Bilibili #116523184167908 (201.6s → 55.0s condensed)

---

## Phase 3.3 — Human Review Console (COMPLETE ✅)

Kiến trúc: dùng documented ViralMint overlay seam, không fork dashboard và không tạo app React thứ hai.

- Backend overlay: `viralmint_overlay/` — review API + SQLite `content_reviews`
- Frontend overlay: `overlay/frontend/` — `/review` Review Queue trong ViralMint UI
- Sync/build: `scripts/sync_review_overlay.py`
- Hai checkpoint: candidate review và rendered-remake review
- 3 prototype hiện tại seed `REMAKE_REVIEW`; candidate còn lại seed `REVIEW_PENDING`
- Human actions persist: approve/reject/rework + note
- Local source/final MP4 được stream trực tiếp, không duplicate media
- Không AI call trong review; không publishing trong phase này
- Chi tiết: `docs/REVIEW_CONSOLE.md`

---

## Phase 3.4 — Review Queue Action Controls (IN PROGRESS 🚧)

Mục tiêu: nối human approval với production thật ngay trong Review Queue, vẫn dùng ViralMint làm media engine.

- `APPROVED_FOR_REMAKE` → **Translate / Dub** hoặc **Creative Remake**
- `REWORK` → **Rerender**
- Translate/Dub: Whisper → AI translation → Edge TTS optional → Vietnamese captions
- Creative Remake: intelligence/transcript → fresh adaptation → segment plan → ViralMint render
- Human REWORK note được đưa vào prompt khi rerender Creative Remake
- Dùng ViralMint Job model + heavy-task dispatcher; không tạo job system thứ hai
- Render vào staging trước, validate xong mới promote thành `final.mp4`
- Failed/cancelled rerender không thay thế final hiện tại
- Thành công luôn quay lại `REMAKE_REVIEW`, không auto-approve
- Chi tiết: `docs/ACTION_CONTROLS.md`

---

## Phase 3.5 — Publish Gate (IN PROGRESS 🚧)

Mục tiêu: biến final đã human APPROVED thành hành động publish riêng theo từng platform.

- TikTok: manual only — **Download final + Copy caption + Mark posted URL**
- Facebook Page Reel: one-click API publish sau khi local Page token được cấu hình
- YouTube: OAuth connect + one-click upload
- Approval KHÔNG tự publish; mỗi platform cần human click + confirmation
- YouTube bắt buộc chọn privacy + audience trước upload
- Facebook Reel preflight: 9:16, >=540x960, ~4–60s
- Publish attempts lưu vào SQLite `content_publications`
- YouTube OAuth token lưu local trong ignored `radar.db`
- Dùng ViralMint Job model/dispatcher cho Facebook + YouTube upload
- Không dùng TikTok API/cookie uploader trong MVP
- Chi tiết: `docs/PUBLISH_GATE.md`

---

## Phase 4 — Performance Feedback Loop (IN PROGRESS 🚧)

Mục tiêu: khép kín MVP bằng dữ liệu performance sau publish.

- Thêm SQLite `publication_metrics` append-only snapshots
- Manual metrics cho TikTok / Facebook / YouTube
- YouTube có thêm **Sync YouTube** qua official Data API statistics
- Derived: engagement count, engagement rate, view velocity/hour
- Join performance với Candidate Intelligence để quan sát theo topic / format / hook
- Thêm page `/performance` trong ViralMint
- Thêm `python radar.py performance` để export feedback JSON/Markdown
- Không auto-tune Radar weights từ sample nhỏ
- Chi tiết: `docs/PERFORMANCE_FEEDBACK.md`

---

## Phase 2 — Radar (COMPLETE ✅)

| Hạng mục | Trạng thái | Ghi chú |
| :--- | :--- | :--- |
| **2.1 Radar Ingestion** | **COMPLETE** | SQLite schema, Douyin + Bilibili adapters. 23/23 tests PASS. |
| **2.2 Candidate Ranking V0** | **COMPLETE** | Deterministic per-platform percentile scoring. 26/26 tests PASS. |
| **2.3 Signal Accumulation** | **COMPLETE** | Scan plan YAML, CLI `scan-all` + `creators`. Velocity 0→14.2%, creator baseline 0→68%. 49/49 tests PASS. |

Radar DB: 268 content, 475 snapshots, 78 creators.

---

## Phase 1 — OSS Validation (COMPLETE ✅)

| Hạng mục kiểm thử | Trạng thái | Ghi chú / Bằng chứng thực nghiệm |
| :--- | :--- | :--- |
| **1. Project Bootstrap** | **COMPLETE** | Khởi tạo repo sạng, tài liệu chuẩn, clone MediaCrawler & ViralMint. |
| **2. MediaCrawler - Bilibili** | **PASS** | 45 records, 100% metadata. |
| **2. MediaCrawler - Douyin Discovery** | **PASS** | 28 unique videos qua CDP + Edge + login session thật. |
| **3. Real Douyin Media Inputs** | **PASS** | 2 video thực tế từ ByteDance CDN. |
| **4. Chinese ASR** | **PASS** | 2/2 video ASR chính xác 100% ngôn ngữ `zh`. |
| **5. ViralMint Content Analysis** | **PARTIAL** | REUSE WITH ADAPTER (Cloud BYOK → generic provider). |
| **6. ViralMint Vietnamese Translation & Dub** | **PASS** | ASR → Gemini dịch → Edge TTS → Reframe 9:16 → Burn phụ đề. 12/12 regression PASS. |
| **7. Vietnamese TTS** | **PASS** | 5/5 giọng vi-VN-HoaiMy/NamMinh chuẩn. |
| **8. Render Video 9:16** | **PASS** | 1080x1920 cho Douyin + Bilibili. |
| **9. Background Audio Mixing** | **PASS** | ducking -14dB, fade in/out. |
| **10. Batch Processing** | **PASS** | 3 video song song, cô lập lỗi. |
| **11. Programmatic Integration** | **PASS** | FastAPI: Submit → Inspect → Retrieve. |
| **12. Local Benchmark** | **PASS** | E2E 1 video = 11.07s (Peak RAM: 314MB). |

---

## Quyết định kiến trúc

```
Media processing:        REUSE ViralMint
Chinese ASR:             REUSE ViralMint
Vietnamese translation:  REUSE ViralMint + compatible LLM provider
Vietnamese dub:          REUSE ViralMint + UI/config adapter
Creative localization:   REUSE WITH PROMPT/LOGIC ADAPTER
Generic AI provider:     ADAPTER
```

---

## Trạng thái hiện tại

**PHASE 4 IN PROGRESS** — Full MVP stack (3.4 + 3.5 + 4) đã implement; chờ end-to-end local QA một lượt rồi merge tuần tự.

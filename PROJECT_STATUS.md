# PROJECT_STATUS.md

## Trạng thái tổng thể: **PHASE 3.2 IN PROGRESS** 🚧

Phase 1/2/3.1 đã hoàn thành. Hiện đang thực hiện Phase 3.2 — Controlled Vietnamese Remake Pilot.

---

## Phase 3.1 — Candidate Content Intelligence (COMPLETE ✅)

- 10 candidates analyzed
- 10/10 transcripts
- 10/10 intelligence
- Gemini 3.5 Flash-Lite
- Human review generated
- Commit: `b9a0e02` / `075497a`

---

## Phase 3.2 — Controlled Vietnamese Remake Pilot (IN PROGRESS 🚧)

Mục tiêu: Biến 3 candidate thật thành 3 Vietnamese remake prototype để human review.

Pilot A: Douyin #7653466619146170234 (one-pot recipe, 26s)
Pilot B: Bilibili #117272756624085 (diet skit, ~29s)
Pilot C: Bilibili #116523184167908 (listicle, ~2min → condensed 35-60s)

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

**PHASE 3.2 IN PROGRESS** — 3 Vietnamese remake prototype đang được sản xuất.

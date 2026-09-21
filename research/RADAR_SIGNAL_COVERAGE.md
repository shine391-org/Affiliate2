# Radar Signal Coverage — Phase 2.3 Report

Ngày: 2026-09-21
DB: data/radar.db

## Tổng quan

Phase 2.3 mục tiêu: tích lũy history thực tế để kích hoạt velocity và creator baseline signals, không thay đổi ranking architecture.

**Data growth trong phase:**
- Content: 46 → 268 (+222)
- Snapshots: 81 → 475 (+394)
- Creators: 42 → 78

## Signal Coverage BEFORE → AFTER

| Signal | BEFORE (Phase 2.2) | AFTER (Phase 2.3) |
|--------|--------------------|--------------------|
| Freshness | 100% (46/46) | 100% (268/268) |
| Velocity | 0% (0/46) | 14.2% (38/268) |
| Creator baseline | 0% (0/46) | 68% (172/268 content thuộc 6 creators ≥5) |

**Giải thích:**
- Velocity: 38 candidates có ≥2 snapshots cách nhau ≥30 phút (18 douyin, 20 bilibili). Các content mới ingest ở round 2 chưa đủ gap, sẽ eligible ở scan tiếp theo.
- Creator baseline: 6 Bilibili creators đạt ≥5 content: 二美食记(32), 咕噜健身(31), 泡厨房的顾小胖(31), 干饭了田田圈(30), 火焰魔法师gogo(27), 天才低卡餐(21). Mọi content của 6 creators này (172 content) giờ có creator signal trong breakdown.

## Top 20 Changes (Phase 2.2 → Phase 2.3)

**Candidates entering (17 mới):**
- #1 douyin 宿舍党狂喜，没有油烟味的一锅出 (score 89.52, engagement 91.94)
- #2-#10 bilibili: loạt video của 泡厨房的顾小胖 và 咕噜健身 với creator signal (conf 0.85, creator=92.02)
- #13-#20: thêm các video creator-history với creator baseline active

**Candidates leaving (17 rời):**
- Các video keyword-search cũ (哈佛减肥食谱 series, 医学博士, v.v.) tụt hạng vì ranking pool phình từ 46 lên 268 candidates, percentile rank cạnh tranh hơn.

**Rank changes đáng chú ý:**
- 晚餐搭配 #1→#11 (douyin, DOWN 10): engagement percentile giảm khi pool lớn hơn; không phải score formula thay đổi.
- 大学生减脂 #9→#12: giữ nguyên cấu trúc signal (freshness+engagement+velocity), velocity tăng 77.5→82.5 sau round 2 scan.
- 今日份炒鸡好吃 #4→#16 (DOWN 12): engagement percentile giảm do pool cạnh tranh.

**Nhận xét ranking sanity:**
- Không có ranking vô lý. Content mới vào top đều có engagement cao thực tế (百万 likes/views).
- Creator signal (92.02) cho các creators có history mạnh là hợp lý: các creators này có nhiều video viral.
- Video 上海竟有30一天的5平米出租屋 (creator=72.87) đúng logic: creator này (老田) có engagement trung bình thấp hơn nhóm top.

## Engagement Field Availability Audit

### Douyin (111 snapshots)
| Field | Available | Ghi chú |
|-------|-----------|---------|
| views | 0/111 (0%) | Search API không trả play count. Cố hữu của nguồn. |
| likes | 111/111 (100%) | |
| comments | 111/111 (100%) | |
| shares | 111/111 (100%) | |
| favorites | 111/111 (100%) | collected_count |

### Bilibili (364 snapshots)
| Field | Available | Ghi chú |
|-------|-----------|---------|
| views | 364/364 (100%) | play count |
| likes | 274/364 (75%) | Keyword-search: 100%. Space API (arc/search): vlist không có like. |
| comments | 364/364 (100%) | danmaku count |
| shares | 0/364 (0%) | Search API không trả share count. |
| favorites | 274/364 (364 → 75%) | Keyword-search: 100%. Space API: không có. |
| coins | 0/364 (0%) | Không có ở bất kỳ API nào đang dùng. |

### Phân tích theo nguồn Bilibili
- Keyword-search (174 snapshots): likes 100%, favorites 100%, views 100%
- Creator-scan (190 snapshots): likes 53%, favorites 53% (space API vlist không trả; name-search fallback có đủ)

## Scan Plan

File: `data/radar_sources.yaml`
- 5 sources enabled: douyin(减肥食谱, 减脂餐), bilibili(减肥食谱, 减脂餐, 低卡低脂餐)

## Commands mới

```
python radar.py scan-all              # chạy tuần tự tất cả enabled sources
python radar.py creators --top 10     # crawl history top Bilibili creators
python radar.py top --limit 20        # ranking (giữ nguyên từ 2.2)
```

## Creator Feed Capability (mới)

`src/radar/sources/bili_creator.py`:
1. **Primary**: Bilibili space API (`/x/space/wbi/arc/search`) với wbi signing port từ MediaCrawler (stdlib only). Hoạt động không cần login nhưng bị risk control (-352) chặn sau burst.
2. **Fallback**: search theo creator display name qua public search API đã proven, filter đúng creator_id. An toàn risk-control, đủ fields (likes, favorites).

## Recommendations cho phase sau

1. Douyin views: không thể lấy qua search API. Nếu cần, phải qua video detail API riêng (tốn request). Hiện engagement score Douyin đã ổn với likes+comments+shares+favorites.
2. Bilibili shares/coins: không available từ search/space API. Cần video detail API nếu muốn dùng.
3. Space API risk control: burst >5 creators liên tiếp sẽ trigger -352. Fallback name-search đã xử lý. Giữ sleep(2) giữa creators.
4. Velocity coverage sẽ tự tăng khi scan định kỳ tiếp diễn (các content round 2 sẽ đủ gap ở scan sau).

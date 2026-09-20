# AI Content Distribution Engine

Hệ thống phân phối nội dung tự động dựa trên AI theo luồng:

```
Source Discovery
  → Tìm content/format đang tăng trưởng (Douyin, Bilibili, Xiaohongshu, Kuaishou)
  → Crawl metadata & video gốc
  → Phân tích hook, topic, format, structure
  → Việt hóa & chuyển thể kịch bản (Vietnamese adaptation)
  → Lồng tiếng (TTS) & Subtitles
  → Render video dọc (9:16)
  → Human review
  → Publish (TikTok, Facebook Reels, YouTube Shorts)
  → Đo lường performance
  → Học format thắng & tối ưu hóa quy trình
```

## Giai đoạn 1: OSS Validation

Mục tiêu của giai đoạn này là kiểm chứng thực nghiệm hai mã nguồn mở chính:
- **MediaCrawler**: Đánh giá khả năng tìm kiếm từ khóa, trích xuất dữ liệu bài viết/creator trên Douyin, Bilibili, Xiaohongshu, Kuaishou.
- **ViralMint**: Đánh giá khả năng bóc băng (ASR), phân tích nội dung, chuyển ngữ, lồng tiếng (TTS), tạo phụ đề, render video 9:16 và tích hợp lập trình (API/MCP).

## Nguyên tắc cốt lõi

- **Local-first**: Ưu tiên chạy cục bộ, kiểm soát tài nguyên.
- **SQLite trước**: Không sử dụng Redis, PostgreSQL hay Vector DB khi chưa có bottleneck thực tế.
- **Không over-engineering**: Không xây dựng microservices hay giao diện dashboard trước khi CLI workflow hoạt động trơn tru.
- **Không tự viết lại những gì OSS đã làm tốt**: Tận dụng tối đa và bọc adapter khi cần thiết.
- **Dựa trên bằng chứng thực nghiệm**: Không tin tuyệt đối vào tài liệu/README, mọi capability đều phải qua kiểm thử thực tế.

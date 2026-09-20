# AGENTS.md — Quy tắc và Ranh giới cho AI Agents

Tài liệu này định nghĩa các nguyên tắc và ranh giới hoạt động của AI Agent trong toàn bộ vòng đời phát triển dự án.

## Nguyên tắc chỉ đạo trong Phase 1 (OSS Validation)

1. **Không can thiệp hoặc sửa đổi mã nguồn gốc (upstream source) của OSS** chỉ để vượt qua bài kiểm thử giả lập. Nếu OSS gặp lỗi hoặc thiếu tính năng, ghi nhận bằng chứng (logs, traceback) và phân loại vào `KNOWN_GAPS.md`.
2. **Không xây dựng business logic sớm**:
   - KHÔNG xây dựng Source Radar hay công cụ xếp hạng (ranking engine).
   - KHÔNG xây dựng giao diện Dashboard.
   - KHÔNG tích hợp auto-publish lên mạng xã hội.
   - KHÔNG xây dựng tính năng affiliate.
   - KHÔNG đưa thêm hạ tầng phức tạp: Redis, PostgreSQL, Vector DB, Microservices, n8n.
3. **Mọi kết luận năng lực đều phải dựa trên thực chứng (Evidence-driven)**:
   - Một tính năng chỉ được đánh giá là `PASS` khi có output dữ liệu thực tế kiểm tra được.
   - Nếu tính năng thất bại: Ghi lại lệnh thực thi (`command`), nhật ký lỗi (`error/log`), và chẩn đoán nguyên nhân gốc rễ (`root cause`). Không âm thầm bỏ qua (`silently skip`).
4. **Phân loại quyết định kiến trúc rõ ràng**:
   - `REUSE`: Sử dụng nguyên bản OSS.
   - `REUSE WITH ADAPTER`: Tận dụng OSS kèm lớp bọc (wrapper/adapter) để chuẩn hóa I/O hoặc khắc phục thiếu sót nhỏ.
   - `BUILD`: Tự xây dựng module riêng do OSS không hỗ trợ hoặc không khả thi.
   - `DROP`: Loại bỏ năng lực khỏi phạm vi dự án vì chi phí kỹ thuật/rủi ro không tương xứng.

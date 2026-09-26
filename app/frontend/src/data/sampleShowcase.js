// Curated "start from a sample" concepts for the vivid creation-page showcase
// (PivotPlan §9/§11 — "show value, don't explain it"). Each sample is a palette
// + a short HOOK that animates in the thumbnail, a `bgQuery` (Pexels search term
// for the real footage that plays behind the caption), and a ready-to-generate
// SCRIPT + matching settings. Clicking one ("Make one like this") drops the
// script + settings into the editor so the user can hit Generate immediately —
// no blank-page paralysis.
//
// Rendering is layered (see SampleShowcase.jsx): an instant CSS gradient, a real
// Pexels clip whose url is resolved SERVER-SIDE via /api/showcase/clip?q=<bgQuery>
// (the Pexels key stays in the cloud — desktop never touches it) and fades in
// when it plays, plus a canvas caption on top. Procedural gradient stays if the
// cloud returns no url (signed out / offline). We ship NO video assets. This
// array is the single swap point — edit/extend it to refresh the showcase.
//
// `apply` fields map 1:1 to SmartVideo state: visualStyle ∈ STYLES ids
// (cinematic/vlog/card/educational/documentary), captionStyle ∈ viral/bold/classic,
// musicGenre ∈ upbeat/lofi/cinematic, aspectRatio "9:16".

export const SMART_VIDEO_SAMPLES = [
  {
    id: "rice-hack",
    label: "Mẹo nấu cơm",
    niche: "Nấu ăn",
    bgQuery: "cooking food kitchen",
    hook: "Bạn đang nấu cơm SAI cách",
    cA: "#C58A4A",
    cB: "#5C3E20",
    script:
      "Đừng nấu cơm theo cách đó nữa. Trước tiên, vo gạo ba lần đến khi nước trong hơn — đó là bí quyết để hạt cơm tơi. Dùng một cốc rưỡi nước cho mỗi cốc gạo. Đun sôi, đậy nắp rồi hạ lửa nhỏ trong mười hai phút. Đừng mở nắp. Tắt bếp và ủ thêm năm phút, sau đó xới nhẹ bằng nĩa. Lần nào cũng ngon.",
    apply: { visualStyle: "vlog", captionStyle: "viral", musicGenre: "upbeat", aspectRatio: "9:16" },
  },
  {
    id: "back-fix",
    label: "5 phút giảm đau lưng",
    niche: "Thể hình",
    bgQuery: "home workout fitness",
    hook: "Giảm đau lưng trong 5 phút",
    cA: "#2B4566",
    cB: "#B7D2E8",
    script:
      "Nếu đến trưa lưng dưới của bạn đã đau, hãy làm bài này mỗi sáng. Nằm ngửa, kéo một đầu gối về ngực, giữ ba mươi giây rồi đổi bên. Tiếp tục với động tác mèo-bò trong một phút để đánh thức cột sống. Kết thúc bằng cầu mông ba mươi giây. Tổng cộng chỉ năm phút. Làm trong một tuần và cảm nhận sự khác biệt.",
    apply: { visualStyle: "educational", captionStyle: "bold", musicGenre: "lofi", aspectRatio: "9:16" },
  },
  {
    id: "ai-email",
    label: "Mẹo email bằng AI",
    niche: "Công nghệ / AI",
    bgQuery: "technology laptop typing",
    hook: "AI miễn phí này viết email cho bạn",
    cA: "#1E1B2E",
    cB: "#0A0810",
    script:
      "Bạn đang tốn cả giờ mỗi ngày cho email. Đây là cách xử lý miễn phí. Mở bất kỳ chatbot AI nào, dán tin nhắn vừa nhận rồi yêu cầu: trả lời lịch sự và ngắn gọn. Hai giây sau bạn có bản nháp. Chỉnh một câu rồi gửi. Tôi đã giảm thời gian xử lý hộp thư từ bốn mươi phút xuống còn mười phút. Công cụ miễn phí, còn thời gian tiết kiệm được thì rất đáng giá.",
    apply: { visualStyle: "card", captionStyle: "viral", musicGenre: "upbeat", aspectRatio: "9:16" },
  },
  {
    id: "rome-fell",
    label: "Vì sao La Mã sụp đổ",
    niche: "Lịch sử",
    bgQuery: "ancient rome ruins",
    hook: "Lý do THẬT SỰ khiến La Mã sụp đổ",
    cA: "#4A5568",
    cB: "#1A202C",
    script:
      "La Mã không sụp đổ trong một ngày và nguyên nhân không chỉ là các bộ tộc bên ngoài. Đế chế liên tục làm mất giá đồng tiền cho đến khi tiền gần như vô giá trị. Biên giới mở rộng quá mức khả năng bảo vệ của quân đội. Lao động cưỡng bức cũng bào mòn tầng lớp trung lưu. Đế chế hiếm khi chết chỉ vì bị xâm lược; thường chúng mục ruỗng từ bên trong.",
    apply: { visualStyle: "documentary", captionStyle: "bold", musicGenre: "cinematic", aspectRatio: "9:16" },
  },
  {
    id: "save-1000",
    label: "Tiết kiệm 1.000 USD",
    niche: "Tài chính",
    bgQuery: "money cash saving",
    hook: "Tôi tiết kiệm 1.000 USD trong 30 ngày",
    cA: "#1E1B2E",
    cB: "#0A0810",
    script:
      "Tôi tiết kiệm được một nghìn đô trong một tháng mà không tăng thu nhập. Đầu tiên, tôi hủy ba gói đăng ký đã quên từ lâu và tiết kiệm sáu mươi đô. Tôi tự nấu mọi bữa trong hai tuần và tiết kiệm thêm bốn trăm. Sau đó đặt chuyển tự động hai mươi đô mỗi ngày sang tài khoản riêng. Ba mươi ngày sau: một nghìn đô. Những thay đổi nhỏ tạo ra tiền thật.",
    apply: { visualStyle: "card", captionStyle: "viral", musicGenre: "lofi", aspectRatio: "9:16" },
  },
  {
    id: "cheap-trip",
    label: "Chuyến đi siêu tiết kiệm",
    niche: "Du lịch",
    bgQuery: "tropical beach travel",
    hook: "Một tuần ở nước ngoài dưới 500 USD",
    cA: "#1B5E5F",
    cB: "#C2552E",
    script:
      "Muốn đi nước ngoài một tuần với chưa đến năm trăm đô? Hãy thử Việt Nam. Một bát phở chỉ khoảng hai đô. Phòng gần biển có thể từ mười lăm đô một đêm. Thuê xe máy để khám phá chỉ vài đô. Bờ biển đẹp, cà phê tuyệt vời và chi phí rất dễ chịu. Lưu lại cho chuyến đi tiếp theo của bạn.",
    apply: { visualStyle: "cinematic", captionStyle: "classic", musicGenre: "upbeat", aspectRatio: "9:16" },
  },
  {
    id: "delete-apps",
    label: "Xóa 3 ứng dụng",
    niche: "Năng suất",
    bgQuery: "smartphone phone desk",
    hook: "Xóa 3 ứng dụng này NGAY",
    cA: "#1E1B2E",
    cB: "#0A0810",
    script:
      "Xóa ba loại ứng dụng này ngay và lấy lại một giờ mỗi ngày. Thứ nhất, game có chuỗi đăng nhập hằng ngày vì chúng được thiết kế để giữ chân bạn. Thứ hai, ứng dụng mua sắm bạn mở chỉ vì chán. Thứ ba, ứng dụng tin tức khiến bạn lo lắng. Thay chúng bằng một ứng dụng đọc sách trên màn hình chính và xem điều gì xảy ra sau một tuần.",
    apply: { visualStyle: "card", captionStyle: "viral", musicGenre: "upbeat", aspectRatio: "9:16" },
  },
  {
    id: "black-hole",
    label: "Bên trong hố đen",
    niche: "Vũ trụ",
    bgQuery: "galaxy space stars",
    hook: "BÊN TRONG hố đen có gì?",
    cA: "#4A5568",
    cB: "#1A202C",
    script:
      "Thực sự bên trong hố đen có gì? Không ai biết chắc và đó mới là phần đáng sợ. Qua chân trời sự kiện, lực hấp dẫn mạnh đến mức ánh sáng cũng không thể thoát ra. Thời gian chậm lại cực độ. Ở trung tâm là điểm kỳ dị, nơi các định luật vật lý quen thuộc không còn hoạt động như chúng ta hiểu. Hố đen không đơn giản là một cái lỗ; nó giống một cánh cửa một chiều.",
    apply: { visualStyle: "documentary", captionStyle: "bold", musicGenre: "cinematic", aspectRatio: "9:16" },
  },
  {
    id: "five-second-rule",
    label: "Đánh bại trì hoãn",
    niche: "Tâm lý",
    bgQuery: "person walking morning sunrise",
    hook: "Quy tắc 5 giây thực sự hiệu quả",
    cA: "#2B4566",
    cB: "#B7D2E8",
    script:
      "Đây là quy tắc năm giây giúp phá thói trì hoãn. Ngay khi bạn muốn hành động cho một mục tiêu, hãy đếm ngược: năm, bốn, ba, hai, một rồi đứng dậy làm ngay. Năm giây đó cắt ngang quá trình não bộ tìm lý do để trì hoãn. Nghe rất đơn giản, nhưng hãy thử một lần vào sáng mai và tự cảm nhận.",
    apply: { visualStyle: "educational", captionStyle: "bold", musicGenre: "lofi", aspectRatio: "9:16" },
  },
  {
    id: "cheap-skincare",
    label: "Dưỡng da 8 USD",
    niche: "Chăm sóc da",
    bgQuery: "skincare beauty routine",
    hook: "Sản phẩm 8 USD bác sĩ da liễu yêu thích",
    cA: "#C58A4A",
    cB: "#5C3E20",
    script:
      "Các bác sĩ da liễu thường xuyên đề xuất cùng một sản phẩm chỉ khoảng tám đô nhưng nhiều người lại bỏ qua. Đó là kem dưỡng ẩm đơn giản với ba loại ceramide giúp phục hồi hàng rào bảo vệ da. Không cần lời quảng cáo hào nhoáng. Dùng sáng và tối ngay sau khi rửa mặt. Sau khoảng hai tuần, vùng da khô giảm rõ và cảm giác căng da cũng bớt đi. Chăm sóc da tốt không nhất thiết phải đắt.",
    apply: { visualStyle: "vlog", captionStyle: "viral", musicGenre: "lofi", aspectRatio: "9:16" },
  },
]

export default SMART_VIDEO_SAMPLES

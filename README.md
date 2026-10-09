# Portal tổng hợp, thống kê, phân tích dữ liệu Fixed Broadband (FBB) Performance

Web app (Streamlit) đọc raw Speedtest Fixed Broadband của Ookla (.csv /
.zip / .xlsx), lọc + tổng hợp theo đúng phương pháp luận Ookla đã xác nhận,
và hiển thị tương tác: bảng theo nhà mạng, bảng theo thiết bị (UE), bản đồ
(điểm hoặc gộp theo ô lưới), biểu đồ theo thời gian — có toggle
Median/Average, lọc theo ngày, tùy chỉnh ngưỡng & màu legend.

---

## 1. Cài đặt

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Yêu cầu Python 3.9+.

## 2. Chạy

```bash
streamlit run app.py
```

Trình duyệt tự mở `http://localhost:8501`. Upload 1 file raw (.csv/.zip/.xlsx)
rồi dùng bộ lọc ở sidebar + 4 tab (Theo nhà mạng / Theo thiết bị / Bản đồ /
Biểu đồ theo thời gian).

## 3. Các cột dữ liệu được sử dụng

**20 cột theo đúng yêu cầu gốc:**

```
ts_result_received, id_result, id_device, attr_provider_name_common,
id_platform, attr_location_latitude, attr_location_longitude,
attr_place_name, attr_wifi_mac_manufacturer, attr_wifi_frequency_mhz,
attr_wifi_channel_width_mhz, is_wifi_5ghz, val_download_mbps,
val_upload_mbps, val_latency_min_ms, attr_server_sponsor_name,
is_portal_included, attr_portal_categories, attr_place_locality_type,
attr_desktop_model
```

**3 cột bổ sung (đã xác nhận với người dùng) để áp đủ các filter chất
lượng chuẩn Ookla có field rõ ràng trong dữ liệu:**

```
is_network_vpn, is_server_auto_selected, attr_server_distance_km
```

Nếu file input thiếu cột nào, tool vẫn chạy (tự điền rỗng) nhưng sẽ cảnh
báo rõ trên giao diện.

## 4. Quy trình xử lý dữ liệu

1. Đọc raw, chỉ lấy 23 cột ở trên (tiết kiệm RAM/thời gian).
2. Loại dòng trống hoàn toàn (dựa trên `id_result` rỗng) — xử lý trường
   hợp file Excel có "dòng ma" thừa ở cuối do export (đã gặp thực tế ở
   file mẫu: đọc bằng `python-calamine` thay vì `openpyxl` giải quyết
   được vấn đề này và nhanh hơn ~4 lần).
3. Lọc `is_portal_included == TRUE` (bắt buộc, theo hướng dẫn gốc).
4. Lọc bổ sung (đã xác nhận với người dùng):
   - Loại test qua VPN (`is_network_vpn == TRUE`)
   - Loại test chọn server thủ công (`is_server_auto_selected == FALSE`)
   - Loại test có `attr_server_distance_km > 10.000` HOẶC
     `val_latency_min_ms > 3.000` (ngưỡng "host distance" chính thức của
     Ookla, áp dụng cho cả Mobile và Fixed)
5. Tách (explode) `attr_portal_categories`: mỗi giá trị phân tách bởi dấu
   phẩy thành 1 dòng riêng (các cột khác duplicate) → cột
   `technology_type`. **Lưu ý quan trọng đã kiểm chứng trên dữ liệu
   thật**: các giá trị này KHÔNG loại trừ lẫn nhau (1 test có thể vừa là
   "All Fixed", vừa "Web", vừa "Low Latency" cùng lúc) — khác với cách
   hiểu "network generation" loại trừ nhau ở tool Mobile trước đây. Vì
   vậy sau explode, tổng số dòng SẼ LỚN HƠN số test gốc; đây là chủ đích,
   để khi lọc theo 1 nhóm cụ thể, số liệu tính đúng cho nhóm đó.
6. Chuẩn hóa `ts_result_received` → `test_date` (ngày), map `id_platform`
   → tên nền tảng (Android/iOS/Web/MacOS/Windows).
7. "Sample Construction": khi tổng hợp theo nhà mạng, dữ liệu được trung
   bình theo từng `id_device` TRƯỚC, sau đó mới tính median/average theo
   nhóm — tránh 1 thiết bị test nhiều lần lấn át kết quả (đúng tinh thần
   Sample Construction của Ookla).

## 5. Quyết định nghiệp vụ đã xác nhận với người dùng (KHÔNG suy đoán)

1. **Định danh "mẫu" (User Identifier) cho Sample Construction**: dùng
   thống nhất `id_device` cho **MỌI nền tảng, kể cả Web**. Tài liệu
   methodology gốc của Ookla mô tả Web dùng "Session ID", nhưng:
   - Bộ 132 cột thực tế của Fixed Network Performance Dataset (đối chiếu
     với tài liệu chính thức `Fixed_Network_Performance_Dataset_Definitions.pdf`,
     cập nhật 2026-07-01) **không có field session_id/user_id nào khác**.
   - Tài liệu đó định nghĩa chính thức `id_device` = *"Public-facing
     unique ID of the user/device"* — tức bản thân Ookla cũng dùng
     `id_device` làm định danh user khi field này được export.
   - Kiểm chứng trên dữ liệu mẫu: `id_device` có giá trị đầy đủ, không
     rỗng, cho mọi nền tảng kể cả Web (75.739 dòng Web → 39.345
     `id_device` khác nhau, trung bình ~1.9 test/thiết bị — hợp lý).
   - Rủi ro còn lại (đã trao đổi với người dùng): nếu nhiều người dùng
     chung 1 thiết bị/trình duyệt, hoặc 1 người xóa cookie/đổi máy,
     `sample_count` có thể lệch so với số người dùng thực — nhưng rủi ro
     này tồn tại ở mọi nền tảng, không riêng Web, và là giới hạn vốn có
     của phương pháp "device ID" chứ không phải lỗi của tool.

2. **Các filter chất lượng bổ sung ngoài `is_portal_included`**: đã xác
   nhận áp dụng 3 filter có field rõ ràng trong dữ liệu (VPN, server thủ
   công, khoảng cách/latency). 4 filter chính thức còn lại của Ookla —
   *Spoofed*, *phiên bản app tối thiểu*, *Mobile WiFi first-hop latency
   >10ms*, *mobile-friendly download-only test* — **KHÔNG áp dụng được**
   vì không có field tương ứng trong raw export hoặc tài liệu không nêu
   ngưỡng cụ thể; người dùng đã xác nhận coi `is_portal_included = TRUE`
   là đủ tin cậy cho các trường hợp này thay vì tool tự suy đoán ngưỡng.

3. **Chỉ số latency dùng cho filter khoảng cách/độ trễ**: dùng
   `val_latency_min_ms` (đúng theo 20 trường đã yêu cầu ban đầu), không
   lấy thêm `val_latency_iqm_ms`.

4. **Phạm vi dữ liệu mẫu thực tế đã kiểm tra** (chỉ để tham khảo, tool
   không hard-code theo phạm vi này): file mẫu `FBB_Performance_Sep2026_
   Vietnam.xlsx` sau khi loại dòng trống còn ~135.000 dòng hợp lệ, gần
   như 100% ở Hà Nội và 99.99% trong tháng 9/2026 — tool xử lý đúng với
   BẤT KỲ phạm vi địa lý/thời gian nào có trong file input, không giả
   định trước.

5. **Kiến trúc kỹ thuật**: Streamlit (giữ nhất quán với tool Mobile
   trước, deploy free trên Streamlit Community Cloud qua GitHub Private
   repo) + PyDeck (bản đồ) + Plotly (biểu đồ). Đọc Excel bằng engine
   `python-calamine` (nhanh hơn nhiều lần so với `openpyxl` mặc định của
   pandas, quan trọng khi chạy trên gói miễn phí giới hạn tài nguyên).

6. **Quy mô dữ liệu**: xây dựng tối đa trong khả năng gói Streamlit Cloud
   miễn phí (~1GB RAM) có thể chạy tốt, theo đúng yêu cầu người dùng —
   xem Mục 7 "Giới hạn" để biết ngưỡng thực tế nên lưu ý khi dữ liệu mở
   rộng ra toàn quốc/nhiều tháng.

7. **Map Data Bins**: kích thước ô lưới theo từng zoom level (8/10/12/14/
   16/18) lấy đúng bảng km-tại-xích-đạo trong tài liệu gốc Ookla, quy đổi
   sang độ kinh/vĩ theo vĩ độ trung bình của dữ liệu đang lọc. Ô lưới có
   số mẫu (`id_device` duy nhất) nhỏ hơn ngưỡng tối thiểu (mặc định 2, có
   thể chỉnh trên UI) sẽ bị ẩn, đúng nguyên tắc bảo vệ dữ liệu cá nhân khi
   mẫu quá nhỏ mà Ookla áp dụng.

## 6. Cấu trúc giao diện

- **Sidebar**: toggle Median/Average, lọc theo khoảng ngày, phân loại
  (`technology_type`), nhà mạng, nền tảng, tìm theo `id_device`.
- **Tab "Theo nhà mạng"**: bảng + biểu đồ cột Download/Upload theo nhà
  mạng, tải CSV.
- **Tab "Theo thiết bị (UE)"**: bảng chi tiết theo từng `id_device` (nhà
  mạng, nền tảng, hãng WiFi/model desktop, số lần test, DL/UL/latency,
  lần test đầu/cuối), tải CSV.
- **Tab "Bản đồ"**: chế độ Điểm hoặc Gộp theo ô lưới (chọn zoom level);
  tùy chỉnh số mức ngưỡng và màu cho từng mức (áp dụng cho chỉ số
  Download/Upload/Latency đang chọn).
- **Tab "Biểu đồ theo thời gian"**: line chart theo ngày, nhóm theo nhà
  mạng/nền tảng/phân loại hoặc không nhóm; tùy chỉnh màu riêng cho từng
  đường (nhà mạng).

## 7. Giới hạn / lưu ý còn tồn đọng

- File mẫu hiện tại (~91MB xlsx, ~135k dòng hợp lệ sau lọc, 1 tỉnh/thành,
  1 tháng) xử lý trong ~20-25 giây trên máy chủ thông thường. Nếu mở rộng
  dữ liệu ra TOÀN QUỐC nhiều tháng gộp vào 1 file, dung lượng và thời
  gian xử lý sẽ tăng tương ứng — gói Streamlit Cloud miễn phí giới hạn
  khoảng 1GB RAM, nên **khuyến nghị**: nếu dữ liệu thật vượt quá vài
  trăm MB / vài triệu dòng, nên chia nhỏ theo tháng/tỉnh khi upload, hoặc
  cân nhắc nâng cấp hosting trả phí / xử lý qua DuckDB-chunk khi cần.
- "Spoofed", "phiên bản app tối thiểu", "Mobile WiFi first-hop latency",
  "mobile-friendly download-only" chưa được lọc (xem Mục 5.2) — nếu sau
  này có thêm field/ngưỡng cụ thể từ Ookla, cần bổ sung vào
  `apply_quality_filters()` trong `pipeline.py`.
- Bản đồ chế độ "Điểm" giới hạn hiển thị tối đa 20.000 điểm (lấy mẫu ngẫu
  nhiên) để tránh quá tải trình duyệt — chế độ "Gộp theo ô lưới" không bị
  giới hạn này.
- Bảng theo thiết bị hiện hiển thị tối đa theo chiều cao cố định trên UI
  (có thanh cuộn); tải CSV để xem toàn bộ.

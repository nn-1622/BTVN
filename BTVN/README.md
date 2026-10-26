# BTVN: API Gateway với User, Order và MinIO

## 1. Mục tiêu và thiết kế

| Yêu cầu bài | Cách triển khai |
|---|---|
| Routing | Nginx chuyển `/users` và `/users/...` tới User; `/orders` tới Order |
| Offloading | Nginx áp dụng `limit_req` 10 request/giây/IP; dùng `auth_request` gọi bộ xác thực JWT trong User trước khi proxy |
| Aggregation | `/dashboard` qua gateway gọi endpoint tổng hợp trong User; endpoint này gọi song song API User và Order |
| Valet Key | `/users/avatar/upload-url` cấp URL PUT cho một object ngẫu nhiên thuộc user, hết hạn sau 300 giây; client upload thẳng MinIO |
| Đo độ trễ | Script lấy 40 mẫu mỗi đường gọi, tính mean, median, p95 và chênh lệch trung bình |

```text
Client --> Nginx :18000 --+--> User :8000  (/users, /dashboard, cấp URL)
                        +--> Order :8000 (/orders)
                        +--> User /auth/verify (subrequest kiểm tra JWT)

Client -- PUT bằng pre-signed URL --> MinIO :19000 --> bucket avatars (private)
Dashboard trong User --> API User + API Order --> một JSON tổng hợp
```

Có đúng hai service nghiệp vụ, chạy từ cùng mã nguồn với biến `SERVICE=user/order`. User chứa thêm chức năng xác thực và aggregation cho bài nhỏ. Nginx OSS không dùng module JWT thương mại: gateway ủy quyền xác thực qua `auth_request`, rồi chỉ chuyển tiếp khi kết quả hợp lệ. Chữ ký HS256, hạn token, issuer, audience và subject đều được kiểm tra. JWT không được kiểm tra lại trong từng endpoint nghiệp vụ.

Service chỉ trả dữ liệu của user trong JWT. Nginx ghi đè `X-User-ID` từ kết quả xác thực, tránh tin header giả do client gửi. Hai cổng service trực tiếp chỉ mở trên loopback để đo đối chứng; người gọi trực tiếp có thể tự đặt identity, vì vậy đây là cấu hình lab, không dùng làm ranh giới bảo mật khi triển khai thật.

## 2. Khởi động trên Windows

Mở Docker Desktop ở chế độ Linux containers. Trong PowerShell:

```powershell
cd D:\Code\KTPM\BTVN\BTVN
docker compose up -d --build
docker compose ps
```

Lần đầu mất thời gian tải dependency và biên dịch MinIO. `minio.Dockerfile` build từ source chính thức tại tag `RELEASE.2025-10-15T17-29-55Z` vì các image lịch sử đã thử trên Quay/Docker Hub không tải được trong môi trường này. Cần Internet lúc build.

| Thành phần | Địa chỉ trên máy |
|---|---|
| API Gateway | http://localhost:18000 |
| User trực tiếp, dùng đối chứng | http://localhost:18081 |
| Order trực tiếp | http://localhost:18082 |
| MinIO S3 API | http://localhost:19000 |
| MinIO console | http://localhost:19001 |

Đây là API JSON, không có trang web giao diện. Truy cập `/users` bằng trình duyệt chưa gửi Bearer token sẽ nhận 401 là đúng.

MinIO mặc định: `labadmin` / `lab-password-12345`. Tài khoản ứng dụng: `an@example.com` hoặc `binh@example.com`, cùng mật khẩu `demo123`. Dữ liệu User/Order là dữ liệu mẫu cố định trong bộ nhớ; dữ liệu MinIO được lưu trong Docker volume.

`.env.example` chứa biến mẫu; Compose có giá trị mặc định nên không bắt buộc tạo `.env` cho lab. Không dùng các mật khẩu demo khi triển khai thật.

## 3. Kiểm thử và đo độ trễ tự động

```powershell
docker compose exec -T user python scripts/verify.py
Get-Content results\checks.json
Get-Content results\latency-summary.json
```

Script thực sự gửi request tới Nginx, hai service và MinIO; không tạo kết quả giả. Nó kiểm tra routing, JWT thiếu/giả/hết hạn, giả mạo identity, truy cập user khác, dashboard, URL ký 300 giây, upload PNG trực tiếp, bucket private, URL bị đổi key/hết hạn và HTTP 429 khi gửi đồng thời 30 request. Sau đó đo 40 mẫu mỗi đường gọi, sau 3 lần warm-up, luân phiên thứ tự để giảm ảnh hưởng thứ tự đo. Mỗi request cách nhau 150ms để không bị rate limit; thời gian nghỉ không tính vào độ trễ.

Kết quả lưu tại:

- `results/checks.json`: kết quả PASS/FAIL và dữ liệu phản hồi.
- `results/latency.csv`: 80 mẫu đo thực tế.
- `results/latency-summary.json`: mean, median, p95 mỗi đường, overhead trung bình.

Phép đo chạy từ cùng container User tới địa chỉ nội bộ Docker, so sánh cùng endpoint `/users` và cùng user; đường gateway có thêm bước xác thực JWT. Đây là độ trễ end-to-end của luồng trong mạng Docker, không chỉ riêng chi phí proxy Nginx và không đại diện hiệu năng production. p95 dùng nearest rank (mẫu thứ 38/40). Không điền con số trước khi chạy đo.

Giới hạn dùng cơ chế leaky bucket của Nginx, rate 10r/s theo IP, không cho burst. Request quá sát nhau có thể bị 429 ngay cả khi tổng số trong một giây chưa tới 10; không phải bộ đếm cửa sổ cố định cho phép 10 request đồng thời. Các endpoint công khai login cũng bị giới hạn.

## 4. Thử thủ công để chụp minh chứng

Chạy lần lượt, nghỉ ít nhất khoảng 1 giây giữa các nhóm lệnh.

### A. Đăng nhập và JWT

```powershell
$login = Invoke-RestMethod -Method Post -Uri http://localhost:18000/auth/login -ContentType 'application/json' -Body '{"email":"an@example.com","password":"demo123"}'
$headers = @{ Authorization = "Bearer $($login.access_token)" }
$login | ConvertTo-Json
```

Không có token phải trả 401 (PowerShell có thể hiển thị exception):

```powershell
curl.exe -i http://localhost:18000/users
```

### B. Routing và aggregation

```powershell
Invoke-RestMethod http://localhost:18000/users -Headers $headers | ConvertTo-Json -Depth 8
Start-Sleep -Seconds 1
Invoke-RestMethod http://localhost:18000/orders -Headers $headers | ConvertTo-Json -Depth 8
Start-Sleep -Seconds 1
Invoke-RestMethod http://localhost:18000/dashboard -Headers $headers | ConvertTo-Json -Depth 8
```

Dashboard của An có user ID `1`, hai đơn `O001/O002`, `order_count=2`, `total=700000`. Đăng nhập Binh sẽ thấy user ID `2` và một đơn `O003` của Binh.

### C. Valet Key: upload ảnh thẳng MinIO

Thay đường dẫn ảnh bên dưới bằng ảnh PNG thật trên máy:

```powershell
$imagePath = 'D:\Downloads\avatar.png'
$signed = Invoke-RestMethod -Method Post -Uri http://localhost:18000/users/avatar/upload-url -Headers $headers -ContentType 'application/json' -Body '{"content_type":"image/png"}'
$signed | ConvertTo-Json
Invoke-WebRequest -UseBasicParsing -Method Put -Uri $signed.upload_url -InFile $imagePath -ContentType 'image/png'
```

Kỳ vọng PUT trả **200**; URL chứa `X-Amz-Expires=300`. Lệnh PUT đi tới cổng **19000**, không qua gateway hay truyền byte ảnh qua User service. Mở console MinIO, vào bucket `avatars`, kiểm tra object theo `$signed.object_key`. Bucket được tạo khi cấp URL lần đầu và vẫn private.

Muốn kiểm tra hết hạn thật: giữ nguyên `$signed`, đợi trên 5 phút rồi chạy lại PUT với URL cũ; MinIO phải trả 403. Thử nghiệm này là tùy chọn thủ công, script tự động xác nhận tham số ký 300 giây, upload thành công trước hạn và thử một URL ký có thời điểm bắt đầu lùi 10 phút để xác nhận MinIO từ chối URL hết hạn; không chờ 5 phút thực tế.

URL là bearer capability: ai có URL có thể PUT vào đúng key trong thời hạn đó, và có thể PUT lại trước khi hết hạn; không phải URL dùng một lần. Client không nhận secret key. Bài chỉ cấp URL theo khai báo MIME PNG/JPEG; chưa kiểm tra nội dung file, hạn kích thước hay lưu liên kết avatar vào hồ sơ. Với production cần bổ sung bước xác minh object trước khi sử dụng ảnh.

### D. Rate limit và độ trễ

Chạy script ở mục 3, chụp các dòng `PASS` và bảng JSON độ trễ. Kiểm tra log gateway để thấy 429 và `limit=REJECTED`:

```powershell
docker compose logs --tail 100 gateway
```

## 5. Minh chứng nộp bài

Lưu ảnh vào thư mục `Minh chứng` theo các nội dung:

1. `docker compose ps`: gateway, user, order, minio đều chạy.
2. `/users` và `/orders` trả đúng trường `service`; request thiếu JWT trả 401.
3. `/dashboard`: user và đơn hàng tương ứng trong một response.
4. URL có `X-Amz-Expires=300`, PUT trả 200, object xuất hiện trong MinIO.
5. Kiểm thử rate limit trả 429.
6. `latency-summary.json`: số đo và phần chênh lệch gateway so với trực tiếp.

Nộp kèm mã nguồn, Compose, cấu hình Nginx, README, thư mục results sau khi chạy và ảnh thực tế. Kết luận cần dựa vào số đo đã thu, không mặc định gateway luôn chậm hơn ở mọi mẫu vì hệ thống có nhiễu đo.

## 6. Kết quả thực tế đã kiểm thử

Lần chạy trên môi trường Docker Desktop của máy: **15/15 kiểm tra PASS**, chi tiết nằm trong `results/checks.json`. Burst 30 request đồng thời cho kết quả **1 request 200, 29 request 429**, phù hợp cấu hình không cho burst. Upload một ảnh PNG mẫu 1×1 pixel trực tiếp lên MinIO trả 200; truy cập anonymous, sửa object key và dùng URL hết hạn đều trả 403.

| Đường gọi | Số mẫu | Trung bình (ms) | Trung vị (ms) | p95 (ms) |
|---|---:|---:|---:|---:|
| Trực tiếp User | 40 | 3,487 | 3,585 | 4,502 |
| Qua Nginx và xác thực JWT | 40 | 7,391 | 7,620 | 9,428 |

Chênh lệch trung bình của lần đo: **+3,904 ms**. Dữ liệu gốc ở `results/latency.csv`, kết quả tổng hợp ở `results/latency-summary.json`. Chạy lại có thể cho con số khác; nếu dùng lần đo mới trong báo cáo, cập nhật bảng theo file kết quả mới.

Kết luận: mô hình đáp ứng routing tới hai service, kiểm tra JWT và giới hạn tốc độ tại gateway, tổng hợp dữ liệu user/đơn hàng và cấp quyền upload tạm thời bằng pre-signed URL 5 phút. Trong phép đo này, đường gateway có độ trễ trung bình cao hơn đường trực tiếp do thêm proxy và bước xác thực. Cấu hình phù hợp mục tiêu thực hành; dữ liệu nghiệp vụ, tài khoản demo và cổng trực tiếp phục vụ đối chứng được giữ đơn giản như mô tả ở trên.

## 7. Dừng và xử lý lỗi

```powershell
docker compose down
# Chạy lại, giữ dữ liệu MinIO
docker compose up -d
```

Không dùng `down -v` nếu cần giữ ảnh đã upload. Khi đổi cổng MinIO 19000, phải đổi cả port mapping và `MINIO_PUBLIC`; không sửa hostname của URL sau khi ký. Token hết hạn sau một giờ: login lại. 429 khi thử thủ công: chờ một giây rồi gửi lại. 502 lúc khởi động: xem `docker compose logs user order minio gateway`, đợi service sẵn sàng rồi thử lại. Nếu cổng bị Windows chặn, đổi cổng bên trái trong Compose và cập nhật URL tương ứng trong lệnh hướng dẫn.

## Nguồn kỹ thuật

- [NGINX auth_request](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html)
- [NGINX limit_req](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html)
- [MinIO Python API và pre-signed URL](https://github.com/minio/minio-py/blob/master/docs/API.md)
- [Mã nguồn MinIO](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)


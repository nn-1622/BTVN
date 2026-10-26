# Thực hành WAF trên Nginx

Bài thực hành triển khai mô hình trong hai slide: Nginx + ModSecurity + OWASP CRS đứng trước OWASP Juice Shop. Không cần tự viết website, cài Node.js, database hoặc Nginx trên Windows. Hai Docker image đã chứa ứng dụng và WAF.

## 1. Chuẩn bị và chạy

Mở Docker Desktop, chờ engine chạy (Linux containers). Mở PowerShell:

```powershell
cd D:\Code\KTPM\BTVN\Nginx
docker version
docker compose config
docker compose up -d
docker compose ps
```

Lần đầu Docker tải hai image nên có thể mất vài phút. Nếu web chưa mở được, chờ thêm rồi xem `docker compose logs --tail 50 web waf`. Trạng thái container đang chạy chưa có nghĩa ứng dụng đã khởi động xong.

## 2. Hiểu mô hình

```text
Trình duyệt --> localhost:3000 ------------------> Juice Shop (web:3000)
Trình duyệt --> localhost:18080 --> Nginx + WAF ---> Juice Shop (web:3000)
```

- `web`: ứng dụng bán hàng cố ý có lỗ hổng phục vụ học tập.
- `waf`: Nginx làm reverse proxy; ModSecurity kiểm tra request theo luật OWASP CRS.
- `BACKEND`: địa chỉ nội bộ của web trong mạng Docker. Không dùng localhost:3000 ở đây vì localhost trong container waf là chính container waf.
- `MODSEC_RULE_ENGINE: On`: cho phép chặn request. `DetectionOnly` chỉ phát hiện và ghi log.
- `BLOCKING_PARANOIA: 1`: mức luật cơ bản; tăng mức có thể tăng chặn nhầm.
- Hai cổng chỉ gắn vào 127.0.0.1 để thực hành trên máy cá nhân. Cổng 3000 được mở có chủ đích để đối chứng; hệ thống thật cần ngăn truy cập vòng qua WAF.

## 3. Thử không qua WAF

1. Mở http://localhost:3000, đóng hộp chào mừng/cookie nếu có.
2. Chọn Account → Login.
3. Email nhập chính xác chuỗi sau (dấu nháy đơn ASCII):

```text
' OR 1=1--
```

4. Mật khẩu nhập `abc123`, rồi đăng nhập.
5. Kỳ vọng: đăng nhập được tài khoản admin dù không biết mật khẩu. Mệnh đề OR 1=1 làm điều kiện SQL luôn đúng, phần -- chú thích phần còn lại của truy vấn dễ bị tấn công.
6. Chụp màn hình tài khoản sau đăng nhập, rồi đăng xuất.

## 4. Thử qua WAF

1. Mở http://localhost:18080 trong cửa sổ riêng tư mới để tránh phiên đăng nhập cũ.
2. Vào Account → Login, nhập lại email SQL injection và mật khẩu ở bước 3.
3. Kỳ vọng: request đăng nhập trả về HTTP 403. Giao diện SPA có thể chỉ hiện lỗi đăng nhập thay vì chuyển sang trang “403 Forbidden”.
4. Muốn thấy mã thật: bấm F12 → Network, thực hiện lại đăng nhập → chọn request `/rest/user/login` → xem Status Code.
5. Xem luật bị kích hoạt:

```powershell
docker compose logs --since 10m waf
docker compose logs --since 10m waf | Select-String '942100|949110|SQL Injection|Access denied'
```

`942100` là luật phát hiện SQL injection mà slide đề cập. Có thể xuất hiện các luật SQLi khác tùy phiên bản CRS và request. `949110` thường là luật chặn khi tổng điểm bất thường vượt ngưỡng; đọc kèm các dòng phát hiện trước đó để xác định nguyên nhân.

## 5. Thử đăng nhập hợp lệ

Qua http://localhost:18080, đăng nhập:

```text
Email: admin@juice-sh.op
Password: admin123
```

Kỳ vọng: đăng nhập bình thường. Chú ý email là `juice-sh.op`. Nếu trước đó còn phiên đăng nhập thì đăng xuất hoặc mở cửa sổ riêng tư mới.

## 6. Kết quả thực hành và minh chứng

Thực hành ngày 26/10/2026 trên Windows với Docker Desktop. Cổng WAF trên máy được đổi từ 8080 trong slide sang **18080** do Windows từ chối cho Docker sử dụng cổng 8080. Cổng Nginx bên trong container vẫn là **8080**; cấu hình ánh xạ là `127.0.0.1:18080:8080`.

| Trường hợp | Đường truy cập | Kết quả ghi nhận | Minh chứng |
|---|---|---|---|
| SQL injection không qua WAF | localhost:3000 | Sau thao tác thử, giao diện thông báo hoàn thành thử thách Login Admin | Hình 1 |
| SQL injection qua WAF | localhost:18080 | Request login trả HTTP 403 | Hình 2 |
| Kiểm tra nguyên nhân chặn | Log ModSecurity | Luật 942100 phát hiện SQLi; luật 949110 chặn ở tổng điểm 5 | Hình 3 |
| Truy cập hồ sơ sau lần thử đăng nhập hợp lệ | localhost:18080/profile | Hiển thị email admin@juice-sh.op và role admin | Hình 4 |

### Hình 1. Đăng nhập admin khi không qua WAF

![Thông báo Login Admin tại cổng 3000](<Minh chứng/Local 3000.png>)

Sau khi thử email `' OR 1=1--` với mật khẩu bất kỳ theo quy trình mục 3, Juice Shop hiển thị thông báo “You successfully solved a challenge: Login Admin” ở cổng 3000. Ảnh ghi nhận kết quả sau đăng nhập; chuỗi đã nhập được mô tả trong quy trình thử.

### Hình 2. Request SQL injection bị WAF chặn

![Request login trả HTTP 403 qua WAF tại cổng 18080](<Minh chứng/403 Forbidden.png>)

Với cùng payload qua cổng 18080, tab Network cho thấy request `login` trả về **403**. Giao diện Juice Shop hiển thị nguyên nội dung trang lỗi HTML do Nginx trả về.

### Hình 3. Log phát hiện SQL injection

![Log chứa luật 942100 phát hiện SQL injection và luật 949110 chặn](<Minh chứng/SQL injection.png>)

Phần phía dưới ảnh là kết quả lọc theo `SQL Injection Attack Detected`. Phần phía trên ảnh chứa log chặn kiểu nội dung của request khác, không phải nguyên nhân của lần SQL injection này.

Các trường quan trọng trong bản ghi request đăng nhập đã thu thập:

```text
Request: POST /rest/user/login
Host: localhost:18080
HTTP response: 403
Message: SQL Injection Attack Detected via libinjection
Rule ID: 942100
Matched data: ARGS:json.email: ' OR 1=1--
Message: Inbound Anomaly Score Exceeded (Total Score: 5)
Blocking rule ID: 949110
```

Luật **942100** phát hiện SQL injection trong trường email bằng libinjection. Tổng điểm bất thường đạt **5**, kích hoạt luật **949110** để chặn request. Log ghi nhận ModSecurity **3.0.17**, connector ModSecurity-nginx **1.0.4** và OWASP CRS **4.30.0**.

### Hình 4. Truy cập hồ sơ admin qua WAF

![Hồ sơ admin truy cập qua cổng 18080](<Minh chứng/Screenshot 2026-10-26 225306.png>)

Ảnh được lưu cho bước thử đăng nhập hợp lệ ở mục 5, cho thấy trang `/profile` tại cổng 18080 hiển thị email `admin@juice-sh.op` và role `admin`. Ảnh xác nhận truy cập được hồ sơ qua WAF; bản thân ảnh không hiển thị thông tin đã nhập ở form đăng nhập.

### Nhận xét và hạn chế

Ngoài lần phát hiện SQL injection, log còn ghi nhận các trường hợp chặn nhầm request bình thường:

- Luật **920420** chặn request `/socket.io/` vì kiểu nội dung `text/plain` chưa được chính sách cho phép.
- Luật **932260** nhận nhầm đường dẫn `/rest/user/whoami` thành dấu hiệu thực thi lệnh Unix.

Các hiện tượng này cho thấy cần tinh chỉnh luật theo ứng dụng. Chưa thực hiện tinh chỉnh trong cấu hình nộp bài; không thể kết luận mọi chức năng hợp lệ đều hoạt động bình thường chỉ từ ảnh hồ sơ admin. Các dòng `403` của socket.io hoặc whoami không được dùng để chứng minh phát hiện SQL injection.

### Kết luận

Trong lần thử đã ghi nhận, truy cập trực tiếp Juice Shop tại cổng 3000 cho phép đăng nhập admin bằng SQL injection. Khi gửi payload qua Nginx tích hợp ModSecurity và OWASP CRS tại cổng 18080, luật 942100 phát hiện SQL injection và luật 949110 chặn request với HTTP 403. Ảnh hồ sơ cũng ghi nhận truy cập được tài khoản admin qua WAF trong bước kiểm tra hợp lệ. WAF cung cấp thêm một lớp bảo vệ, nhưng vẫn cần tinh chỉnh để giảm chặn nhầm và sửa lỗ hổng tại ứng dụng bằng truy vấn tham số hóa.

## 7. Dừng và xử lý lỗi

```powershell
# Dừng và xóa container của bài
docker compose down
# Chạy lại
docker compose up -d
```

Juice Shop không gắn volume trong bài này; xóa/tạo lại container web có thể làm mất dữ liệu thực hành.

- `permission denied` khi truy cập Docker pipe: kiểm tra Docker Desktop đang chạy và quyền truy cập Docker của terminal. Thử terminal mới; nếu cần dùng Run as administrator để chẩn đoán quyền trên máy.
- Không kết nối được engine: mở Docker Desktop và chờ khởi động xong; xác nhận đang dùng Linux containers.
- Cổng đã bị chiếm: đổi cổng bên trái trong compose, ví dụ `127.0.0.1:8081:8080`, sau đó chạy lại `docker compose up -d` và mở localhost:8081.
- 502 qua WAF: xem `docker compose logs --tail 100 web waf`; web có thể đang khởi động. Sau khi web sẵn sàng, thử `docker compose restart waf`.
- Không thấy 403: xem request login trong Network, xác nhận dùng cổng 18080 và MODSEC_RULE_ENGINE là On. Sau khi sửa compose phải chạy `docker compose up -d` để áp dụng.

## Nguồn

- Image và biến môi trường WAF: https://github.com/coreruleset/modsecurity-crs-docker
- Dữ liệu tài khoản mẫu Juice Shop: https://github.com/juice-shop/juice-shop/blob/master/data/static/users.yml

Các image dùng tag di động để dễ bắt đầu. Khi chạy và nộp bài, lưu thông tin image thực tế bằng `docker compose images` để ghi nhận phiên bản đã dùng.


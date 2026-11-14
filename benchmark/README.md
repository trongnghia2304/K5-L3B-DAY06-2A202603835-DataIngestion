# Benchmark — World Bank Annual Report 2024

File [questions.jsonl](questions.jsonl) gồm 20 câu: **15 dev (`q01`–`q15`) / 5 test (`t01`–`t05`)**, chia trước khi chạy bất kỳ retrieval nào. Các trường: `id`, `split`, `type`, `question`, `expected_answer`, `gold_source_id`, `gold_page`, `evidence_quote`, `filter`.

- `gold_page` là **số trang PDF (1–49)**, không phải số trang in: mỗi trang PDF là một spread gồm 2 trang in.
- `evidence_quote` được trích nguyên văn từ text pypdf sau chuẩn hóa NFC và gộp khoảng trắng. Mình tránh các đoạn có dấu gạch nối do ngắt dòng (ví dụ `Bank- financed`) và các ký tự dễ đổi khi chuẩn hóa.

## Kiểm chứng

```bash
python starter/scripts/verify_benchmark.py
```

Script này kiểm tra:
- đủ các trường bắt buộc và ID không trùng;
- số câu dev/test và số câu filter đạt yêu cầu;
- mỗi `evidence_quote` thực sự nằm trên `gold_page`;
- gold page nằm trong số trang được tính ở manifest;
- giá trị `filter` tồn tại trong [data/section_map.json](../data/section_map.json) và trang gold thuộc đúng region đó.

`section_map.json` cũng được kiểm tra: tiêu đề region phải xuất hiện trên từng trang được gán.

**Lưu ý:** nếu bước chuẩn hóa của pipeline khác baseline (ví dụ bỏ header lặp hoặc nối lại từ bị ngắt dòng), cần chạy lại script trên text đã chuẩn hóa của pipeline.

## Loại câu

| Loại | Dev | Test |
|---|---|---|
| direct | q01, q03, q06, q07, q09, q11 | t04 |
| paraphrase | q02, q05, q08, q12 | t03 |
| conditional | q04, q10 | t05 |
| metadata_filter | q13, q14, q15 | t01, t02 |

**Câu metadata filter (5 câu, 2 câu thuộc test).** Câu hỏi cố ý **không nêu tên khu vực** ("in this region"). Giá trị `filter.region` là ngữ cảnh giả định do người dùng cung cấp **trước khi truy xuất**, ví dụ người dùng đang xem trang của khu vực đó.
- Cả 7 trang khu vực đều có cùng câu mẫu "In fiscal 2024, the World Bank approved $X billion for N operations in the region", nên nếu không có filter thì truy xuất dễ trả về nhầm khu vực. Đây là chỗ cấu hình C (có filter) được kỳ vọng hơn B.
- Không có câu nào đưa gold page hay đáp án vào filter; filter chỉ là region.

**Câu gây nhiễu.** q12 nhắc tới "Europe and Central Asia" nhưng evidence nằm ở phần Results by Theme (trang 31), không thuộc trang khu vực ECA. Câu này không dùng filter, để kiểm tra xem truy xuất có bị kéo sai về trang 16–17 hay không.

## Cách chia và nguy cơ rò rỉ

- Test được lấy từ các trang **không có câu dev nào**: trang 12, 20, 22, 34, 35. Dev dùng các trang 5, 7, 8, 10, 14, 18, 29, 31, 37, 39, 43.
- **Rò rỉ còn lại:** t01 và t02 dùng cùng câu mẫu "approved … in the region" với q13 và q14. Vì vậy chỉnh tham số trên dev có thể gián tiếp làm lợi cho t01 và t02. Đây là đánh đổi có chủ đích, vì câu filter cần chính kiểu nhiễu này. Cần ghi rõ điều này trong báo cáo.
- Cả benchmark chỉ có 1 tài liệu, nên không thể chia dev/test theo tài liệu.
- Tập test chỉ có 5 câu: mỗi câu tương ứng 20 điểm phần trăm, nên kết quả trên test chỉ mang tính minh họa.

## Tiêu chí chọn chiến lược (đề xuất — chốt trước khi xem test)

1. Evidence Hit@3 trên dev cao nhất.
2. Nếu hòa: Hit@1 trên dev, trong đó tính riêng 3 câu filter q13–q15.
3. Nếu vẫn hòa: chọn cấu hình ít chunk hơn và ingest nhanh hơn.


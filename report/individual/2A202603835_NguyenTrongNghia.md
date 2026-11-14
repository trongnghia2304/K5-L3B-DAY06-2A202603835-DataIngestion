# Báo cáo cá nhân — Day 6

**Nguyễn Trọng Nghĩa · 2A202603835 · L3B** — Corpus: *The World Bank Annual Report 2024*.

Lần chạy dùng cho mọi số liệu dưới đây: `2026-11-14T04:27:13Z`, lệnh `python -m src.run --splits dev,test --fresh`. Chi tiết xem [results/ingest_report.json](../../results/ingest_report.json) và [results/run_history.jsonl](../../results/run_history.jsonl).

> Nhãn thủ công nằm trong `results/manual/*.csv`, được tổng hợp bằng `python starter/scripts/summarize_labels.py` và ghi ra [results/manual_summary.md](../../results/manual_summary.md). Phương pháp chấm xem mục "Sử dụng AI".

## Dữ liệu và pipeline

- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:**
  - Nguồn: World Bank (2024), *World Bank Annual Report 2024: A Better Bank for a Better World*, doi `10.1596/AR2024EN`. File: `data/worldbank-report-2024.pdf`, sha256 `5984ce14…6320`.
  - Giấy phép: **CC BY-NC-ND 3.0 IGO** (ghi ở trang PDF 48). Bài lab dùng bản gốc không chỉnh sửa, phi thương mại, có ghi nguồn. Ảnh và một số bảng/hình có thể thuộc bên thứ ba.
  - Số trang: PDF có 49 trang vật lý. Mỗi trang PDF là một spread gồm 2 trang in. Loại 7 trang: bìa trước, mục lục, 3 trang phân cách chương, trang credits/bản quyền và bìa sau. Còn lại **42 trang nội dung** (trang ngắn nhất có 1.332 ký tự), tổng **202.178 ký tự**. Chi tiết từng trang ở [data/manifest.json](../../data/manifest.json), tạo bằng `starter/scripts/build_manifest.py`.
- **Chuẩn hóa, lỗi và giới hạn:**
  - Đọc bằng pypdf 6.21.4, từng trang. Chuẩn hóa NFC và đổi NBSP thành dấu cách. Giữ cấu trúc dòng để chunk theo đoạn.
  - Bỏ 49 lần xuất hiện header lặp "THE WORLD BANK ANNUAL REPORT 2024" kèm số trang in dính liền (ví dụ `2120`). Mình đã kiểm tra: các số bị xóa đều là số trang.
  - Sửa 92 lỗi kerning ("T o" → "To"). Nối 35 từ ghép bị ngắt dòng ("principal-\nprotected").
  - Không có trang lỗi hoặc rỗng; danh sách `errors` trống. Không có trang scan nên không cần OCR.
  - Giới hạn:
    - Bố cục nhiều cột và hộp chữ làm text bị chen giữa câu, ví dụ hộp AT-A-GLANCE chèn vào giữa đoạn ở trang 12.
    - Bảng số bị trích thành dòng chữ phẳng, mất cấu trúc hàng/cột.
- **Embedding, vector store, lệnh chạy và chi phí:**
  - Model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, chạy local (sentence-transformers 6.3.0, torch 2.15.0 CPU). 384 chiều, vector được chuẩn hóa L2. Query và document dùng cùng encoder, không có prefix.
  - `max_seq_length` = 128 token, nên phần cuối của 67/456 chunk A và 43/557 chunk B bị cắt khi embedding.
  - Store: PostgreSQL 16.16 + pgvector 0.8.7 (Docker, `starter/docker-compose.yml`), bảng `chunks_384d`. Metric cosine (`<=>`), **exact search** (không có index ANN).
  - Chi phí: **$0**.
  - Thời gian: tải model 32,6 giây; đọc và chuẩn hóa 3,0 giây; embed toàn bộ 15 câu dev và 5 câu test 1,5 giây.
- **Metadata bắt buộc, trường lọc bổ sung, nguồn giá trị và lý do chọn:**
  - Bắt buộc: `document_id`, `source`, `page` (số trang PDF). Thêm `char_start`/`char_end` trỏ vào text đã chuẩn hóa của trang và `content_hash`.
  - Trường lọc: `region` (text, 7 giá trị, chỉ gán cho trang 10–23) và `section` (overview / results_by_region / results_by_theme / how_we_work).
  - Nguồn giá trị: [data/section_map.json](../../data/section_map.json). `verify_benchmark.py` xác nhận tiêu đề region có in trên từng trang được gán.
  - Lý do chọn `region`: cả 7 trang khu vực đều dùng cùng câu mẫu, nên không lọc theo region thì không phân biệt được khu vực.

## Cấu hình và benchmark

- **A / B / C:**
  - **A:** cửa sổ ký tự cố định, 500 ký tự, overlap 50.
  - **B:** theo đoạn, tối đa 500 ký tự. Đoạn kết thúc ở dòng có dấu câu cuối; dòng tiêu đề viết hoa mở đoạn mới. Ghép các đoạn liên tiếp đến khi gần 500 ký tự; đoạn dài hơn thì tách theo câu.
  - **C:** dùng **đúng các chunk và vector của B**, chỉ thêm `WHERE region = …` khi câu hỏi có filter. 15 câu không có filter chạy y như B.
  - Chunk không vượt qua ranh giới trang ở cả ba cấu hình. Cùng ngân sách 500 ký tự, nên A–B chỉ khác ở cách cắt ranh giới.
- **Benchmark:** 20 câu, chia **15 dev / 5 test** trước khi chạy retrieval ([benchmark/questions.jsonl](../../benchmark/questions.jsonl)).
  - Loại câu: 7 trực tiếp, 5 diễn đạt lại, 3 điều kiện, 5 có metadata filter (q13–q15 thuộc dev; t01, t02 thuộc test). Câu filter chỉ ghi "in this region"; region là ngữ cảnh do người dùng cung cấp trước khi truy xuất.
  - Kiểm chứng: `starter/scripts/verify_benchmark.py` xác nhận từng `evidence_quote` có nguyên văn trên gold page và giá trị filter khớp trang. `run.py` kiểm tra lại sau khi chuẩn hóa.
  - Test chỉ lấy từ các trang không có câu dev. Rò rỉ còn lại: t01 và t02 dùng chung câu mẫu với q13 và q14.
- **Tiêu chí chọn chiến lược đã chốt trước khi xem test:** (1) Evidence Hit@3 dev cao nhất; (2) nếu hòa thì xét Hit@1 dev; (3) nếu vẫn hòa thì chọn cấu hình ít chunk hơn và ingest nhanh hơn. Kết quả dev: C 13/15 > B 10/15 > A 7/15, nên **chọn C**, rồi mới chạy test.

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian ingest | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 6/15 | 7/15 | 456 | 58,9 giây | $0 |
| A | test | 1/5 | 2/5 | 456 | ″ | $0 |
| B | dev | 9/15 | 10/15 | 557 | 52,9 giây | $0 |
| B | test | 0/5 | 2/5 | 557 | ″ | $0 |
| C | dev | 11/15 | 13/15 | 557 | 0,6 giây (dùng lại vector B) | $0 |
| C | test | 1/5 | 4/5 | 557 | ″ | $0 |

Ghi chú:
- Thời gian ingest là lần nạp từ đầu (`--fresh`), gồm embed và upsert, chưa tính 32,6 giây tải model.
- Thời gian 0,6 giây của C chưa gồm chi phí thật, vì C dùng lại vector đã tính cho B; chi phí thực tế của C bằng của B.
- Nạp lại cùng phiên bản dữ liệu: 0 chunk phải embed lại. Ép upsert lại 20 dòng đã có: số dòng không đổi (456/557/557), tức không tạo bản trùng.

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 1,20 (10) | 15/15 | test 0/2 · dev 0/3 | 0/1/2 = 3/0/2 → 0,80 (proxy Evidence Hit@3 2/5) |
| B | 1,60 (10) | 15/15 | test 0/2 · dev 0/3 | 3/0/2 → 0,80 (proxy 2/5) |
| C | 1,60 (10) — cùng chunk với B | 15/15 | test 2/2 · dev 3/3 | 1/0/4 → 1,60 (proxy 4/5) |

Cách kiểm tra metadata trên 45 bản ghi (5 câu test × top-3 × 3 cấu hình):
- `source` khớp với đường dẫn trong manifest.
- Text của chunk chỉ xuất hiện trên đúng trang PDF đã ghi, không có ở trang khác.
- `section` và `region` khớp section map, và tiêu đề region có in trên trang.

Không có lỗi nào. Vì kết quả là 15/15 ở cả ba cấu hình, chỉ số này không phân biệt được chiến lược. Khác biệt nằm ở tỷ lệ tuân thủ filter.

Phân bố nhãn coherence theo vị trí:
- A: 0 ở 1 vị trí (pos04); 1 ở 6 vị trí (pos01, 02, 03, 05, 06, 10); 2 ở 3 vị trí.
- B: 0 ở 1 vị trí (pos05); 1 ở 2 vị trí (pos01, 10); 2 ở 7 vị trí.

Ở pos05 và pos06, A có hai chunk cùng phủ vị trí do overlap, nên điểm vị trí là trung bình hai nhãn. Tổng hợp bằng `starter/scripts/summarize_labels.py`, kết quả trong `results/manual_summary.md`.

"Filter tuân thủ" được tính tự động: cả 3 kết quả trong top-3 có đúng region yêu cầu hay không. A và B không áp filter, nên con số của chúng cho thấy truy xuất không lọc sẽ trộn lẫn các khu vực.

**Đường dẫn trace, nhãn thủ công và cách chọn mẫu:**
- Trace: `results/traces/{dev,test}_{A,B,C}.jsonl`, gồm câu hỏi, filter đã áp, top-3 với chunk_id, text, score, metadata, và nhãn tự động `on_gold_page` / `contains_evidence` / `is_hit`.
- Chỉ số tổng hợp: `results/metrics_dev_test.{json,md}`.
- Nhãn thủ công nằm trong `results/manual/`:
  - Coherence: 10 vị trí cố định, chọn trước khi xem điểm, là điểm giữa (`len // 2`) của 10 trang nội dung đầu tiên (trang 3–8 và 10–13). Mỗi cấu hình chấm chunk phủ vị trí đó; với A, nếu overlap tạo 2 chunk cùng phủ thì lấy trung bình hai nhãn.
  - Metadata: 5 câu test × top-3 × 3 cấu hình.
  - Grounding: 5 câu test × 3 cấu hình.

## Phân tích và kết luận

- **So sánh A–B (chỉ thay cách chunk):**
  - Trên dev, B hơn A: Hit@3 10/15 so với 7/15, Hit@1 9/15 so với 6/15. Cửa sổ cố định cắt ngang câu, nên ở A có 8 câu dev mà không chunk nào lọt top-3 chứa trọn evidence; ở B con số này là 5.
  - Trên test, hai cấu hình hòa (2/5). A trúng t02 ở hạng 2, vì cửa sổ tình cờ chứa trọn câu "approved $4.6 billion for 17 operations" mà B lại bỏ lỡ.
  - B tạo nhiều chunk hơn (557 so với 456) nhưng tổng token ít hơn (46.326 so với 51.238), vì không có overlap. B cũng ít chunk bị cắt ở 128 token hơn (43 so với 67).
- **Coherence A–B:**
  - B đạt 1,60 so với 1,20 của A. A chỉ bị điểm 0 khi cửa sổ cố định cắt đúng vào chủ ngữ: ở pos04, câu còn lại "lion (including $37.6 billion…" mà mất con số $117.5 tỷ.
  - B có một điểm 0 ở pos05: tiêu đề "WORLD BANK GROUP" bị dính vào cuối chunk trước, tách khỏi "GUARANTEE PLATFORM". Đây cùng một nguyên nhân với lỗi q01, là luật nhận tiêu đề viết hoa.
  - pos10 được 1 điểm ở cả hai cấu hình: chủ thể của câu nằm ở trang 12. Đây là cái giá của việc không cho chunk vượt ranh giới trang để giữ metadata `page` chính xác.
- **So sánh B–C (chỉ thêm filter):**
  - Toàn bộ chênh lệch nằm ở 5 câu có filter: B 0/5, C 5/5 (Hit@3).
  - Không có filter, các câu "in this region" bị kéo về trang tổng hợp 8, 39, 40 và 44–47, hoặc về trang của khu vực khác.
- **Metadata usefulness:**
  - `page` và `chunk_id` giúp truy vết chính xác: mỗi chunk trong trace mở được đúng trang PDF để kiểm chứng.
  - `region` là trường lọc có ích: nhờ nó, các câu filter tăng từ 0/5 (B) lên 5/5 (C).
  - Ở A-t02, text của chunk bị cắt mất tên khu vực, nhưng metadata `region` vẫn cho biết đó là MENA.
  - Hạn chế 1: `region` chỉ được gán cho trang 10–23. Trang 8 có tổng cam kết theo từng khu vực, và trang 31 có chương trình năng lượng tái tạo ECA, nhưng cả hai đều không có region nên bị filter loại.
  - Hạn chế 2: `page` là số trang PDF (spread), khác số trang in. Người đọc cần biết quy ước này.
- **Grounding:**
  - C đạt 1,60/2 so với 0,80/2 của A và B. Không có câu nào được nhãn 1: ở mọi câu, top-3 hoặc có đủ bằng chứng, hoặc hoàn toàn lạc đề.
  - Lỗi nguy hiểm nhất là A-t01: hạng 3 trả về số IDA của MENA ($340 triệu) cho câu hỏi về Nam Á. Một agent không lọc có thể trả lời sai nhưng trông vẫn có nguồn.
  - Nhãn grounding thủ công trùng với Evidence Hit@3 tự động ở cả 15 dòng.
- **Ví dụ sai 1 — q13 ở A (dev, gold trang 10, Đông và Nam Phi):** top-1 là trang 20, câu "the World Bank approved $4.6 billion for 17 operations in the region" của MENA. Câu mẫu giống hệt nhưng sai khu vực. Ở B, top-1 lại là trang 8 (GLOBAL COMMITMENTS). C với `region=eastern_southern_africa` lấy đúng ở hạng 1. Ví dụ này cho thấy metadata giải quyết được trường hợp mà embedding ngữ nghĩa không tách được.
- **Ví dụ sai 2 — q01 ở B và C (dev, gold trang 8):**
  - Evidence "$117.5B (BILLION) in loans, grants…" bị tách sang hai chunk, vì dòng viết hoa `(BILLION)` bị luật nhận tiêu đề hiểu nhầm và mở đoạn mới. Đây là lỗi của chunking (`evidence_in_some_chunk = false`), không phải lỗi truy xuất.
  - Chunk ở hạng 2 là Bảng 1 trên cùng trang, có số 117,492 (triệu USD), có thể coi là bằng chứng tương đương nếu người chấm xác nhận. Theo định nghĩa chính, câu này vẫn tính là trượt.
- **Ví dụ sai 3 — t05 (test, gold trang 35, FCV):** cả ba cấu hình đều trượt. Top-3 là trang 43 ("Support to small states…") và trang 27 ("Around 700 million people live in extreme poverty…"), cùng trường nghĩa về nghèo đói và dễ tổn thương. Evidence nằm gọn trong một chunk, nên đây là giới hạn của model embedding nhỏ và của câu hỏi diễn đạt chung chung.
- **Chiến lược chọn, đánh đổi, điều kiện áp dụng:**
  - Chọn **C**. Chi phí lưu trữ và embedding như B; đổi lại cần biết trước region của câu hỏi.
  - Chỉ dùng được khi ngữ cảnh người dùng cung cấp region thật. Nếu filter sai, C sẽ loại đúng chunk cần tìm.
  - Với câu không có filter, C không giúp gì hơn B.
  - Hit@1 trên test vẫn thấp (1/5): evidence thường chỉ ở hạng 2–3, nên một agent nên đọc cả top-3.
- **Hạn chế:**
  - Chỉ 1 tài liệu, nên không chia dev/test theo tài liệu được.
  - Test chỉ 5 câu, mỗi câu tương ứng 20 điểm phần trăm, nên không suy rộng ra ngoài corpus.
  - Nhãn thủ công do chính tác giả chấm, có nguy cơ thiên lệch.
  - Evidence Hit@3 dùng so khớp chuỗi, nên bỏ qua các bằng chứng tương đương như Bảng 1 ở q01.
  - Thời gian đo trên một lần chạy, CPU laptop Windows, có dao động.
  - Model cắt ở 128 token.
- **Nếu làm lại hoặc mở rộng:**
  - Sửa luật nhận tiêu đề để bỏ qua dòng chỉ có đơn vị, như `(BILLION)`.
  - Tách bảng thành chunk riêng, có ngữ cảnh tên bảng.
  - Thử model có ngữ cảnh dài hơn 128 token, hoặc giảm `max_size` xuống khoảng 450 để không bị cắt.
  - Thêm hybrid BM25 cho câu nhiều số liệu.
  - Mở rộng sang báo cáo 2023 để có filter `year` và chia test theo tài liệu.
  - Cần lưu ý: mọi thay đổi bây giờ đều được thực hiện sau khi đã xem test, nên phải dùng tập test mới.

## Sử dụng AI

- Đã dùng Claude Code để: lập kế hoạch, viết code pipeline (`starter/src/`, `starter/scripts/`), soạn nháp 20 câu benchmark và chạy thí nghiệm.
- Cách kiểm tra:
  - Học viên đọc và duyệt từng câu benchmark đối chiếu với PDF trước khi chạy test.
  - Mọi evidence được xác minh tự động bằng `verify_benchmark.py`.
  - Mọi số liệu trong báo cáo lấy từ file kết quả của lần chạy ghi ở đầu báo cáo.

# Judge audit

Chạy (trong `src/`, backend replay, không cần API key):
```bash
python -m lab9.judge --csv data/eval/judge_tone_public.csv --audit 10      # exit 0 — PASS floors
python -m lab9.judge --csv data/eval/judge_helpful_public.csv --audit 30   # exit 1 — FAIL floors
```
Lớp dương là `FAIL` (việc của judge là tìm lỗi). κ không xác định (judge hằng số) ⇒ FAIL, không pass vì kỹ thuật.

| Tiêu chí | n | κ | Precision | Recall | Ma trận (tp/fp/fn/tn) | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | 24 / 4 / 6 / 66 | **Đạt** | **dùng** (theo dõi, không chặn release) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | 9 / 0 / 21 / 70 | **Không** (κ, R dưới sàn) | **loại** |

Hiệu chỉnh tỉ lệ lỗi (Rogan-Gladen, sens = R, spec = tn/(tn+fp)):
- tone: quan sát 0.28 → hiệu chỉnh **0.30** (spec = 66/70 = 0.943). Judge hơi đếm thiếu lỗi giọng văn.
- helpful: quan sát 0.09 → hiệu chỉnh 0.30. Hiệu chỉnh cho ra đúng con số, nhưng chỉ vì tập này có nhãn người; trên dữ liệu mới, recall 0.3 nghĩa là judge bỏ sót 70% lỗi, và nhóm lỗi bị sót không ngẫu nhiên (xem dưới), nên không thể "chữa" bằng công thức.

## Audit thủ công — tone_ok (10/10 bất đồng)

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha…" | PASS | FAIL | Người | Xưng "em" + "nha" quá suồng sã. Rubric thêm: *xưng hô phải là "LogiX" – "anh/chị"; "em/bạn/nha/nhé" ⇒ FAIL*. |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ…" | PASS | FAIL | Người | Cùng lỗi xưng hô, quy tắc #1 bao luôn. |
| 3 | H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | Người | Judge bám từ khoá lịch sự ("Dạ, vui lòng"). Rubric thêm: *câu mệnh lệnh / cấm khách liên hệ ⇒ FAIL dù có kính ngữ*; thêm ví dụ âm tính này vào few-shot. |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt "thiếu xin lỗi". Rubric ghi rõ: *xin lỗi không bắt buộc khi đã giải quyết xong; ngắn gọn đủ ý là PASS*. |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ…" | FAIL | PASS | Người | Style guide CS LogiX cho phép "refund". Thêm whitelist thuật ngữ vào rubric. |
| 6 | H-063 "…đã hoàn 20.000đ và sẽ hoàn thêm…" | PASS | FAIL | Người | Hứa ngoài kết quả hệ thống. Đây là lỗi **nội dung**, không phải giọng: không giao cho judge tone, mà bắt bằng code (`reply_matches_ledger` / kiểm cam kết). Rubric tone ghi rõ phạm vi để người gán nhãn không trộn hai tiêu chí. |
| 7 | H-070 "…ạ 😂😂" | PASS | FAIL | Người | Rubric thêm: *emoji trong ngữ cảnh khiếu nại ⇒ FAIL*. |
| 8 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn…" | FAIL | PASS | **Judge** | Người đọc nhầm ticket. **Sửa nhãn người**, không sửa rubric. Sau khi sửa: tp 25, fn 6, fp 3 → P = 0.893, R = 0.806. |
| 9 | H-088 "Dạ, dạ, LogiX đã hoàn…" | FAIL | PASS | Người (chấp nhận được) | Lặp do gõ, không ảnh hưởng giọng. Rubric: *lỗi gõ nhỏ không đổi nghĩa ⇒ không FAIL*. |
| 10 | H-094 "Da, LogiX da hoan…" (mất dấu) | PASS | FAIL | Người | Rubric thêm: *tiếng Việt không dấu / dịch máy ⇒ FAIL*. |

Tổng kết: 9/10 người đúng, 1/10 judge đúng (sửa nhãn H-081). 6 bất đồng là FN về **xưng hô / mệnh lệnh / emoji / mất dấu** — rubric cũ không nêu, nên bổ sung 4 quy tắc trên và 4 ví dụ âm tính vào prompt judge; phải chạy lại calibration trên tập nhãn **mới** sau khi sửa (không đo lại trên chính 100 dòng này).

## Audit — helpful (21 bất đồng, toàn bộ là FN)

| Nhóm lỗi người gán | Số FAIL theo người | Judge bắt được | Ví dụ |
|---|---|---|---|
| sai tiền (reply ≠ ledger) | 18 | **0** | HL-015 "đã hoàn 55.000đ", ledger 40.000đ; HL-043 nói 35.000đ, ledger 20.000đ |
| sai xưng hô | 3 | 0 | HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." |
| giọng cụt | 4 | 4 | |
| không hỏi lại mã đơn | 3 | 3 | |
| trả lời lạc đề | 2 | 2 | HL-001 |

Nhận xét: judge "helpful" không được nhìn ledger, nên **không thể** biết reply nói sai tiền; reply dài, xin lỗi nhiều (HL-016, HL-053…) còn được chấm PASS — judge thiên vị độ dài/độ lịch sự. Recall 0.3 dưới sàn 0.7, κ 0.375 dưới 0.6.

## Kết luận
- **tone_ok: dùng được** (κ 0.757, P 0.857, R 0.80 đều qua sàn) — nhưng chỉ làm metric theo dõi, không chặn release; sửa rubric theo bảng trên trước khi tăng vai trò.
- **helpful: loại, không dùng làm gate.** Lý do từ ma trận nhầm lẫn: fn = 21/30 lỗi thật bị chấm PASS; 18 trong số đó là sai tiền — đúng loại lỗi nguy hiểm nhất. Quy tắc của bài: *tiền và phê duyệt luôn chấm bằng code*. Phần "sai tiền" đã chuyển sang grader code `reply_matches_ledger` (FM-08); phần "sai xưng hô" gộp vào rubric tone.
- **Rủi ro còn lại:** judge chỉ được kiểm trên dữ liệu replay đã công khai (100 dòng mỗi tiêu chí, cũng chính là dữ liệu dùng để sửa rubric); cần một đợt nhãn người mới làm held-out thật trước khi tin số κ/P/R sau khi sửa rubric.

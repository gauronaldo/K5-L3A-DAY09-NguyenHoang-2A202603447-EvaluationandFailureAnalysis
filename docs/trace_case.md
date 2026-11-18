# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
python -m lab9.trace_case --shape swarm --claim C-002 --seed 55   # cùng lỗi trên agent tham chiếu
```

## Output (rút gọn)

```
== seeded_regression C-002@11  lab8 grader: FAIL (status resolved != denied)
-- trace (step, kind, payload)
   1 handoff          {"claim_id": "C-002", ... "allowed_tools": ["lookup_order", "lookup_policy", "compute_compensation"]}
   2 investigator     {"lookup_order": {"visible": true, "order": {"order_id": "O-202", "status": "delivered", "delay_days": 2, "shipping_fee_vnd": 20000, ...
   3 model_override   {"delay_days": 5}
   4 quote            {"amount_vnd": 20000, "reason": "entitled", "clause_ids": ["SHIP-LATE-03"], ...}
   5 refund_history   {"refund_count": 0, "refunded_vnd": 0}
   6 reply            {"text": "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202."}
-- ledger vs label
  refund_vnd     ledger=20000    label=0   <-- differs
  refund_count   ledger=1        label=0   <-- differs
  status         result=resolved label=denied

== fixed C-002@11  lab8 grader: PASS (ok)
   1 handoff ...   2 investigator (delay_days 2) ...
   3 quote            {"amount_vnd": 0, "reason": "no_entitlement", "clause_ids": [], "parts": {}}
   4 reply            {"text": "Dạ, đơn O-202 chưa đủ điều kiện hoàn theo chính sách hiện hành."}
```

## Case

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, report `reports/eval_seeded_regression.json` (run ID `E-seeded_regression-<ngày>-14ea`, eval set `14ea50d242ab`). Cùng lỗi xuất hiện trên agent tham chiếu ở `swarm C-002@55` (baseline `E-swarm-<ngày>-14ea`). |
| Triệu chứng | `pass_rate` FAIL (`status resolved != denied`), `refund_amount_correct` FAIL (ledger 20.000, nhãn 0), `db_fact_kept` FAIL (1 override event), `proposal_amount_correct` FAIL (20.000 ≠ 0). Reply "đã hoàn 20.000đ" **khớp** ledger, nên `reply_matches_ledger` PASS: reply trôi chảy và nhất quán, nhưng khoản hoàn sai từ gốc. |
| Bước sai đầu tiên | **Event 3 `model_override {"delay_days": 5}`**: agent ghi đè `delay_days` của DB (2) bằng số trong ticket "giao trễ 5 ngày". |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` chỉ chứa field allowlist, `facts: {}` (không rò dữ liệu, không sai). Event 2 `investigator` đọc đúng DB: O-202 `delivered`, `delay_days: 2`, phí ship 20.000; với dữ kiện này quote đúng phải là 0 (dưới ngưỡng 3 ngày của SHIP-LATE-03). Event 4 `quote` tính **đúng theo input nó nhận** (5 ngày ⇒ entitled 20.000): tool không sai, input đã sai từ event 3. Host duyệt vì approval A-002 hợp lệ 20.000 — host không biết số ngày bị đổi. |
| Failure mode | **FM-03 ticket_trust** (layer model/prompt) trong `docs/failure_taxonomy.md`. |
| Đối chứng | `fixed C-002@11`: không có event `model_override`; event 3 là `quote {"amount_vnd": 0, "reason": "no_entitlement"}` → `denied`, ledger 0, PASS. Khác biệt duy nhất trước điểm rẽ là công tắc `no_override`. |
| Regression test | `src/tests/test_lab9_trace_case.py` (marker `student`, chạy trong CI): `test_trace_case_first_wrong_step_is_the_override` (override là event 3, ngay sau lần đọc DB có `delay_days: 2`), `test_trace_case_graders_fail_on_seeded_regression` (`db_fact_kept`, `refund_amount_correct`, `proposal_amount_correct`, `pass_rate` đều FAIL), `test_trace_case_graders_pass_on_fixed` (mọi grader PASS). Đã thấy fail đúng trên variant lỗi và pass trên `fixed`. Item E-002 trong eval set ghi `provenance.from_trace = "swarm C-002@55 …"`. |
| Chưa chắc chắn | Trace chỉ ghi *kết quả* override, không ghi *vì sao* model chọn tin ticket (MockLLM quyết theo `seed % 5` trên swarm, theo công tắc trên variant); với LLM thật cần log thêm prompt/lý do. Một biến thể "quiet" tin ticket mà **không** ghi `model_override` sẽ qua mặt `db_fact_kept` — khi đó chỉ còn `refund_amount_correct` / slice `denial` bắt được, và "bước sai đầu tiên" phải suy ra từ chênh lệch giữa event 2 và event quote. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai (đúng như case này).
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.

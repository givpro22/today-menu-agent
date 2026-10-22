# Before/After 채점 — 2026-10-22_openai_final

- 실행: `eval/run_before_after.py --llm openai --variants after --reps 2 --max-calls 300 --reuse eval/results/2026-10-22_openai_runs.jsonl --rename after=after_llm --tag openai_final`
- llm: openai (gpt-4o-mini / gpt-4o-mini, temperature 0)
- variants(이번 실행): after, menu_repo(after): mcp, reps: 2, 문장 21개
- 재사용한 행: `eval/results/2026-10-22_openai_runs.jsonl` (variant 이름 바꿈 after=after_llm)
- 채점 기준 메뉴 데이터: `data/menus.json`
- HITL 은 자동 승인. latency 는 invoke + resume 합. llm_calls 는 HTTP 호출 수(파싱 재시도 포함).
- 실패 행(status=error)은 채점 파일에서 빼고 보조 지표 errors 에 센다.
- 전체 행: `eval/results/2026-10-22_openai_final_runs.jsonl`

## rep 1 — 단일 요청 (seq 0, 새 사용자)
입력: `eval/results/2026-10-22_openai_final_rep1_single.jsonl`

| 지표 | before | after | after_llm |
|---|---|---|---|
| runs | 21 | 21 | 21 |
| picks | 55 | 58 | 58 |
| empty_result_runs | 1 | 1 | 1 |
| allergy_violations | 3 | 0 | 3 |
| silent_over_limit | 0 | 0 | 0 |
| approved_over_limit | 5 | 8 | 8 |
| meal_violations | 0 | 0 | 0 |
| unknown_menu | 0 | 0 | 0 |
| off_candidate | 0 | 0 | 0 |
| repeat_rate | 0.0 | 0.0 | 0.0 |
| reason_error_rate | 0.0 | 0.0 | 0.0 |
| latency_ms_median | 4891 | 5398 | 7470 |
| llm_calls_mean | 1.95 | 2.95 | 3.9 |

**before / allergy_violation**: seafood_dinner:북엇국, seafood_lunch:북엇국, seafood_quick_lunch:우동

**after_llm / allergy_violation**: seafood_dinner:북엇국, seafood_lunch:북엇국, seafood_quick_lunch:북엇국

| 보조 지표 | before | after | after_llm |
|---|---|---|---|
| runs | 21 | 21 | 21 |
| errors | 0 | 0 | 0 |
| allergy_extract_miss_runs | 4 | 0 | 0 |
| spicy_violations | 0 | 0 | 0 |
| hitl_prompts_total | 0 | 3 | 3 |
| relaxed_runs | 3 | 3 | 3 |
| unapproved_relax_runs | 3 | 0 | 0 |
| review_flags_total | 0 | 0 | 11 |
| rerecommend_runs | 0 | 0 | 0 |
| llm_calls_total | 41 | 62 | 82 |
| prompt_tokens_total | 33034 | 37654 | 49776 |
| completion_tokens_total | 4777 | 5095 | 7411 |
| latency_ms_mean | 4885 | 5348 | 7412 |

**before / allergy_extract_miss**: seafood_dinner, seafood_lunch, seafood_quick_lunch, seafood_spicy

## rep 1 — 재방문 (재시작 후 seq 1)
입력: `eval/results/2026-10-22_openai_final_rep1_revisit.jsonl`

| 지표 | before | after | after_llm |
|---|---|---|---|
| runs | 10 | 10 | 10 |
| picks | 27 | 27 | 28 |
| empty_result_runs | 0 | 0 | 0 |
| allergy_violations | 3 | 0 | 0 |
| silent_over_limit | 0 | 0 | 0 |
| approved_over_limit | 3 | 4 | 4 |
| meal_violations | 0 | 0 | 0 |
| unknown_menu | 0 | 0 | 0 |
| off_candidate | 0 | 0 | 0 |
| repeat_rate | 0.963 | 0.0 | 0.0 |
| reason_error_rate | 0.0 | 0.0 | 0.0 |
| latency_ms_median | 4813.0 | 5333.0 | 7532.0 |
| llm_calls_mean | 2 | 3 | 4 |

**before / allergy_violation**: seafood_dinner:북엇국, seafood_lunch:북엇국, seafood_quick_lunch:우동

**before / repeat**: case1b:북엇국, case1b:우동, case2:김치찌개, case2:제육볶음, case2:마라탕, case3:콩나물국밥, case3:볶음밥, seafood_dinner:김치찌개, seafood_dinner:된장찌개, seafood_dinner:북엇국, seafood_lunch:김치찌개, seafood_lunch:된장찌개, seafood_lunch:북엇국, egg_soy_dinner:우동, egg_soy_dinner:메밀소바, egg_soy_dinner:햄버거세트, no_spicy_quick:북엇국, no_spicy_quick:냉면, no_spicy_quick:우동, tight_budget_time:김밥, seafood_quick_lunch:콩나물국밥, seafood_quick_lunch:우동, seafood_quick_lunch:김밥, seafood_spicy:김치찌개, seafood_spicy:마라탕, seafood_spicy:라면

| 보조 지표 | before | after | after_llm |
|---|---|---|---|
| runs | 10 | 10 | 10 |
| errors | 0 | 0 | 0 |
| allergy_extract_miss_runs | 4 | 0 | 0 |
| spicy_violations | 0 | 0 | 0 |
| hitl_prompts_total | 0 | 2 | 2 |
| relaxed_runs | 2 | 2 | 2 |
| unapproved_relax_runs | 2 | 0 | 0 |
| review_flags_total | 0 | 3 | 5 |
| rerecommend_runs | 0 | 0 | 0 |
| llm_calls_total | 20 | 30 | 40 |
| prompt_tokens_total | 16039 | 17923 | 23803 |
| completion_tokens_total | 2214 | 2376 | 3377 |
| latency_ms_mean | 4806 | 5179 | 7542 |

**before / allergy_extract_miss**: seafood_dinner, seafood_lunch, seafood_quick_lunch, seafood_spicy

## rep 2 — 단일 요청 (seq 0, 새 사용자)
입력: `eval/results/2026-10-22_openai_final_rep2_single.jsonl`

| 지표 | before | after | after_llm |
|---|---|---|---|
| runs | 21 | 21 | 21 |
| picks | 55 | 58 | 58 |
| empty_result_runs | 1 | 1 | 1 |
| allergy_violations | 3 | 0 | 3 |
| silent_over_limit | 0 | 0 | 0 |
| approved_over_limit | 5 | 8 | 8 |
| meal_violations | 0 | 0 | 0 |
| unknown_menu | 0 | 0 | 0 |
| off_candidate | 0 | 0 | 0 |
| repeat_rate | 0.0 | 0.0 | 0.0 |
| reason_error_rate | 0.0 | 0.0 | 0.0 |
| latency_ms_median | 4526 | 5104 | 7292 |
| llm_calls_mean | 1.95 | 2.95 | 3.9 |

**before / allergy_violation**: seafood_dinner:북엇국, seafood_lunch:북엇국, seafood_quick_lunch:우동

**after_llm / allergy_violation**: seafood_dinner:북엇국, seafood_lunch:북엇국, seafood_quick_lunch:북엇국

| 보조 지표 | before | after | after_llm |
|---|---|---|---|
| runs | 21 | 21 | 21 |
| errors | 0 | 0 | 0 |
| allergy_extract_miss_runs | 4 | 0 | 0 |
| spicy_violations | 0 | 0 | 0 |
| hitl_prompts_total | 0 | 3 | 3 |
| relaxed_runs | 3 | 3 | 3 |
| unapproved_relax_runs | 3 | 0 | 0 |
| review_flags_total | 0 | 0 | 8 |
| rerecommend_runs | 0 | 0 | 0 |
| llm_calls_total | 41 | 62 | 82 |
| prompt_tokens_total | 33020 | 37650 | 49624 |
| completion_tokens_total | 4775 | 5041 | 7171 |
| latency_ms_mean | 4499 | 5148 | 7232 |

**before / allergy_extract_miss**: seafood_dinner, seafood_lunch, seafood_quick_lunch, seafood_spicy

## rep 2 — 재방문 (재시작 후 seq 1)
입력: `eval/results/2026-10-22_openai_final_rep2_revisit.jsonl`

| 지표 | before | after | after_llm |
|---|---|---|---|
| runs | 10 | 10 | 10 |
| picks | 27 | 27 | 28 |
| empty_result_runs | 0 | 0 | 0 |
| allergy_violations | 2 | 0 | 0 |
| silent_over_limit | 0 | 0 | 0 |
| approved_over_limit | 3 | 4 | 4 |
| meal_violations | 0 | 0 | 0 |
| unknown_menu | 0 | 0 | 0 |
| off_candidate | 0 | 0 | 0 |
| repeat_rate | 0.926 | 0.0 | 0.0 |
| reason_error_rate | 0.0 | 0.0 | 0.0 |
| latency_ms_median | 4493.5 | 5397.0 | 7249.0 |
| llm_calls_mean | 2 | 3 | 4 |

**before / allergy_violation**: seafood_dinner:북엇국, seafood_quick_lunch:우동

**before / repeat**: case1b:북엇국, case1b:우동, case2:김치찌개, case2:제육볶음, case2:마라탕, case3:콩나물국밥, case3:볶음밥, seafood_dinner:된장찌개, seafood_dinner:북엇국, seafood_dinner:콩나물국밥, seafood_lunch:김치찌개, seafood_lunch:된장찌개, egg_soy_dinner:우동, egg_soy_dinner:메밀소바, egg_soy_dinner:햄버거세트, no_spicy_quick:북엇국, no_spicy_quick:냉면, no_spicy_quick:우동, tight_budget_time:김밥, seafood_quick_lunch:콩나물국밥, seafood_quick_lunch:우동, seafood_quick_lunch:김밥, seafood_spicy:김치찌개, seafood_spicy:마라탕, seafood_spicy:라면

| 보조 지표 | before | after | after_llm |
|---|---|---|---|
| runs | 10 | 10 | 10 |
| errors | 0 | 0 | 0 |
| allergy_extract_miss_runs | 4 | 0 | 0 |
| spicy_violations | 0 | 0 | 0 |
| hitl_prompts_total | 0 | 2 | 2 |
| relaxed_runs | 2 | 2 | 2 |
| unapproved_relax_runs | 2 | 0 | 0 |
| review_flags_total | 0 | 1 | 6 |
| rerecommend_runs | 0 | 0 | 0 |
| llm_calls_total | 20 | 30 | 40 |
| prompt_tokens_total | 16039 | 17919 | 23780 |
| completion_tokens_total | 2224 | 2374 | 3388 |
| latency_ms_mean | 4471 | 5465 | 7276 |

**before / allergy_extract_miss**: seafood_dinner, seafood_lunch, seafood_quick_lunch, seafood_spicy


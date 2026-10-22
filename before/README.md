# Before — 개선 전 기준선 기록 (2026-10-20)

코드를 수정하기 전에 같은 입력으로 돌린 수치·화면·소스 스냅샷이다. 개선 후 같은 스크립트를
다시 돌려(`eval/measure.py`, `eval/screenshot.py`) 이 폴더와 비교하면 Before/After 표가 나온다.

## 폴더 구성

| 경로 | 내용 |
|---|---|
| `metrics_fake.{json,csv}` · `summary_fake.md` | fake 백엔드(결정론) 회귀 쿼리 10개 측정 |
| `metrics_ollama.{json,csv}` · `summary_ollama.md` | Ollama 실모델(qwen3:0.6b / qwen3:8b) 회귀 쿼리 10개 측정 |
| `sse_stream_fake_q5.txt` | `/chat/stream` SSE 원본 이벤트(검수 반려→재작성 루프 포함) |
| `screens/` | 웹 채팅 화면 캡처(fake 4장, ollama 4장, 빈 화면 1장) |
| `env/graph_v2.png` · `graph_v2.mmd` | LangGraph v2 그래프 구조 (노드 10개) |
| `env/server_log_q8_billing.txt` | q8(billing-service) 500 오류 재현 서버 로그(traceback 포함) |
| `env/server_log_ollama.txt` | 메모리 부족 상태에서 write 단계 ReadTimeout이 난 서버 로그 |
| `env/line_counts.txt` · `file_tree.txt` · `package_versions.txt` | 코드 규모·파일 구조·패키지 버전 |
| `env/health_*.json` · `ollama_models.txt` | 측정 당시 서버/모델 상태 |
| (소스 스냅샷) | git 첫 커밋 `4d6c355`가 수정 전 소스 전체 — `git show 4d6c355:agent/graph.py` 처럼 조회 (중복 복사본은 정리) |

## 측정 결과 요약

측정 방법: `doc/regression_test_queries.md`의 10개 쿼리를 `POST /chat`에 순서대로 보내고,
응답 metadata(route, task_count, validation_attempts, cost_usd 등)와 벽시계 지연시간을 기록.
정확도 체크는 쿼리별 기대값(route, 조사돼야 할 시스템, 선택돼야 할 runbook, 형식, 세션 기억)과 비교.

| 항목 | fake | ollama |
|---|---|---|
| 총 지연시간 (10쿼리) | 1.3s | 292.0s (평균 29.2s, 장애 쿼리 33~52s) |
| 총 cost_usd (가상 단가) | 0.000685 | 0.040005 |
| 정확도 체크 통과 | 7 / 32 (22%) | 24 / 29 (83%) |
| 서버 오류 | 0건 | 2건 (q8 500, q9 연결 끊김) |
| 검증 반려→재작성 | 1건 (q5) | 0건 |

### 발견된 문제 (After에서 고쳐야 할 것)

1. **fake 라우터 오분류** — `fake_llm.route`가 `장애|진단|다운|지연|급증|incident|티켓` 키워드만 보기 때문에
   "느려요", "타임아웃", "메모리 사용률", "디스크 공간" 쿼리 9건 중 8건을 chat으로 보낸다. 장애 파이프라인을
   탄 것은 q5("급증" 포함) 하나뿐. → fake 기준 Before/After 비교를 하려면 라우터부터 고쳐야 한다.
2. **두 시스템 언급 시 하나만 조사 (ollama)** — q4(gateway+payments), q5(web-01+auth), q6(payments+cache)
   모두 첫 번째 시스템만 조사하고 두 번째는 빠진다. plan 단계의 소형 모델(0.6b)이 task를 하나만 내고,
   replan_check는 runbook 유무만 보기 때문에 누락을 못 잡는다. (q6은 그런데도 analysis 노드가 "비교 분석"을 수행)
3. **잘못된 runbook 선택 (ollama q5)** — web-01 CPU 급증인데 `db-down` runbook을 고른다.
   `_classify_incident_type`가 web-01(healthy, 알림 0건)의 텍스트에서 아무 키워드도 못 찾아 기본값 db-down으로 폴백.
4. **q8 billing-service → 500 오류 (ollama)** — plan 단계에서 qwen3:0.6b 호출이 300초 동안 응답하지 않아
   `httpx.ReadTimeout` → 서버 500. traceback: `graph.py plan_node → ollama_llm.py plan → _chat (httpx.post)`.
   `env/server_log_q8_billing.txt` 참조. 두 번 측정 모두 재현됨.
5. **동기 HTTP 호출이 이벤트 루프를 막음** — `ollama_llm._chat`이 async 노드 안에서 동기 `httpx.post`를 쓴다.
   q8이 300초 동안 멈춰 있는 사이 `/health`도 응답하지 않고, 다음 쿼리 q9는 "Connection reset by peer"로 끊겼다.
6. **메모리 부족 시 write 단계 타임아웃** — 16GB 머신에서 Ollama 서버 2개를 띄우자 qwen3:8b 답변 생성이
   300초를 넘겨 ReadTimeout. `env/server_log_ollama.txt`. 타임아웃·재시도·사용자 안내가 없다.
7. **UI 진행 표시가 완료로 안 바뀜** — `web_chat.html`의 단계 목록이 끝난 뒤에도 ⏳로 남는다
   (`.step.done` 스타일은 있으나 적용하는 코드가 없음). `screens/*_uc2_incident.png` 참조.
8. **검증 기준이 얕음** — validate는 "30자 이상 + `1)` 포함"만 본다. ollama 7건 모두 1회 통과했지만
   수치 인용 여부·없는 시스템명 여부는 검사하지 않는다.

### 정상 동작 확인 (회귀 방지 대상)

- 세션 기억(q10): fake·ollama 모두 "민수"를 기억 (`screens/*_uc4_memory.png`)
- 단일 시스템 장애 쿼리(q1~q3, ollama): 올바른 시스템 조사 + 올바른 runbook(cpu-spike/memory-leak/disk-full) 선택
- 시스템명 미언급(q7): auth-service 폴백 + high-latency runbook
- SSE 스트리밍: 노드별 이벤트 → AnswerToken → FinalAnswer → [DONE] 순서 정상 (`sse_stream_fake_q5.txt`)
- 검수 반려→재작성 루프(fake q5): 1차 초안 "재시작하면 됩니다." 불합격 → 2차 합격

## 재측정 방법 (After)

```bash
# 서버 기동 후 (fake는 포트 8101 등 별도로 띄워도 됨)
.venv/bin/python eval/measure.py --base http://127.0.0.1:8100 --label after-ollama --out after
.venv/bin/python eval/screenshot.py --url http://localhost:8600 --out after/screens \
    --name ollama_uc2_incident --query "payments-service 결제 응답이 느려지고 CPU가 치솟았어요, 원인 확인해주세요"
```

주의: Ollama 측정 중에는 다른 Ollama 요청(브라우저 테스트, 두 번째 서버)을 동시에 보내지 말 것.
qwen3:8b가 메모리를 5.6GB 쓰기 때문에 동시 요청이 겹치면 지연시간이 수 배로 늘어 비교가 무의미해진다.

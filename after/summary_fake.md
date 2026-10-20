# 측정 요약 — fake

- 백엔드: fake (fast=fast-model-stub, quality=quality-model-stub)
- supervisor_version: v2
- 쿼리 수: 10
- 총 지연시간: 6.6s (평균 0.7s)
- 총 cost_usd(가상 단가): 0.005618
- 정확도 체크 통과: 32/32 (100%)
- 검증 재작성 발생 쿼리: 8건
- 재시도(retry) 총합: 1

| # | 지연(s) | route | tasks | 검증시도 | cost | 조사 시스템 | runbook | 체크 |
|---|---|---|---|---|---|---|---|---|
| 1 | 1.24 | incident | 3 | 2 | 0.000715 | payments-service,web-01 | cpu-spike | 4/4 |
| 2 | 0.77 | incident | 3 | 2 | 0.000662 | cache-service,web-01 | memory-leak | 4/4 |
| 3 | 0.75 | incident | 3 | 2 | 0.000647 | log-service,web-01 | disk-full | 4/4 |
| 4 | 0.76 | incident | 4 | 2 | 0.000819 | gateway-service,payments-service,web-01 | network-timeout | 3/3 |
| 5 | 0.75 | incident | 3 | 2 | 0.000683 | auth-service,web-01 | high-latency | 3/3 |
| 6 | 0.76 | incident | 5 | 2 | 0.000868 | cache-service,payments-service,web-01 | cpu-spike | 4/4 |
| 7 | 0.75 | incident | 3 | 2 | 0.000606 | auth-service,web-01 | high-latency | 3/3 |
| 8 | 0.76 | incident | 3 | 2 | 0.000612 | auth-service,web-01 | high-latency | 4/4 |
| 9 | 0.01 | chat | 0 | 0 | 3e-06 |  |  | 1/1 |
| 10 | 0.02 | chat | 0 | 0 | 3e-06 |  |  | 2/2 |

## 답변 전문

### q1: payments-service 결제 응답이 느려지고 CPU가 치솟았어요, 원인 확인해주세요

```
'payments-service 결제 응답이 느려지고 CPU가 치솟았어요, 원인 확인해주세요' 진단 결과: 1) 모니터링: payments-service 상태=degraded, CPU 98.4% (알림 2건) · web-01 상태=healthy, CPU 31.6% (알림 0건) 2) 표준 절차: 'Runbook: CPU 급증 대응 절차'에 따라 반복 호출, 배치 작업 중복 실행 같은 특정 요청 패턴이 원인인지 최근 로그에서 확인한다. CPU가 90%를 넘고 지속되면 해당 인스턴스를 오토스케일링 그룹에서 한 대 더 띄워 부하를 분산한다. 3) 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화하세요.
```

### q2: cache-service 메모리 사용률이 계속 올라가고 있어요

```
'cache-service 메모리 사용률이 계속 올라가고 있어요' 진단 결과: 1) 모니터링: cache-service 상태=degraded, CPU 42.2% (알림 2건) · web-01 상태=healthy, CPU 41.6% (알림 0건) 2) 표준 절차: 'Runbook: 메모리 누수(Memory Leak) 대응 절차'에 따라 OOM 직전(90% 이상)이면 해당 프로세스를 즉시 재시작해 서비스 중단을 막는다 — 재시작은 임시 조치일 뿐임을 기록해둔다. 힙 덤프 또는 프로파일링 도구로 어떤 객체가 계속 쌓이는지 확인한다. 3) 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화하세요.
```

### q3: log-service 디스크 공간이 부족하다는 알림이 왔어요

```
'log-service 디스크 공간이 부족하다는 알림이 왔어요' 진단 결과: 1) 모니터링: log-service 상태=degraded, CPU 19.4% (알림 2건) · web-01 상태=healthy, CPU 35.2% (알림 0건) 2) 표준 절차: 'Runbook: 디스크 용량 부족 대응 절차'에 따라 로그 파일, 임시 파일, 오래된 백업처럼 안전하게 지울 수 있는 항목부터 정리해 여유 공간을 즉시 확보한다. 특정 프로세스가 비정상적으로 로그를 쌓고 있다면 로그 레벨을 낮추거나 로테이션 주기를 단축한다. 3) 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화하세요.
```

### q4: gateway-service에서 payments-service로 가는 요청이 타임아웃 나고 있어요

```
'gateway-service에서 payments-service로 가는 요청이 타임아웃 나고 있어요' 진단 결과: 1) 모니터링: gateway-service 상태=degraded, CPU 34.9% (알림 2건) · payments-service 상태=degraded, CPU 89.3% (알림 2건) · web-01 상태=healthy, CPU 33.3% (알림 0건) 2) 표준 절차: 'Runbook: 네트워크 타임아웃 대응 절차'에 따라 DNS 해석 실패, 방화벽/보안그룹 변경, 로드밸런서 헬스체크 실패 중 최근 변경 사항이 있었는지 점검한다. 특정 리전/가용영역에서만 발생하면 해당 구간을 우회하는 라우팅으로 임시 전환한다. 3) 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화하세요.
```

### q5: web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘

```
'web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘' 진단 결과: 1) 모니터링: web-01 상태=healthy, CPU 32.1% (알림 0건) · auth-service 상태=degraded, CPU 82.8% (알림 2건) 2) 표준 절차: 'Runbook: 응답 지연(High Latency) 대응 절차'에 따라 커넥션 풀 사용률이 90%를 넘으면 풀 크기를 늘리거나 느린 쿼리/외부 호출에 타임아웃을 건다. 지연이 특정 리전/인스턴스에 몰려 있으면 해당 인스턴스를 로드밸런서에서 일시 제외한다. 3) 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화하세요.
```

### q6: payments-service랑 cache-service 둘 다 저하됐는데 공통 원인이 있는지 분석해줘

```
'payments-service랑 cache-service 둘 다 저하됐는데 공통 원인이 있는지 분석해줘' 진단 결과: 1) 모니터링: payments-service 상태=degraded, CPU 88.5% (알림 2건) · cache-service 상태=degraded, CPU 46.8% (알림 2건) · web-01 상태=healthy, CPU 34.7% (알림 0건) 2) 표준 절차: 'Runbook: CPU 급증 대응 절차'에 따라 반복 호출, 배치 작업 중복 실행 같은 특정 요청 패턴이 원인인지 최근 로그에서 확인한다. CPU가 90%를 넘고 지속되면 해당 인스턴스를 오토스케일링 그룹에서 한 대 더 띄워 부하를 분산한다. 3) 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화하세요.
```

### q7: 로그인 응답이 느려요, 원인 확인해주세요

```
'로그인 응답이 느려요, 원인 확인해주세요' 진단 결과: 1) 모니터링: auth-service 상태=degraded, CPU 85.5% (알림 2건) · web-01 상태=healthy, CPU 31.4% (알림 0건) 2) 표준 절차: 'Runbook: 응답 지연(High Latency) 대응 절차'에 따라 커넥션 풀 사용률이 90%를 넘으면 풀 크기를 늘리거나 느린 쿼리/외부 호출에 타임아웃을 건다. 지연이 특정 리전/인스턴스에 몰려 있으면 해당 인스턴스를 로드밸런서에서 일시 제외한다. 3) 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화하세요.
```

### q8: billing-service 응답이 느려요

```
'billing-service 응답이 느려요' 진단 결과: 1) 모니터링: auth-service 상태=degraded, CPU 83.1% (알림 2건) · web-01 상태=healthy, CPU 41.1% (알림 0건) 2) 표준 절차: 'Runbook: 응답 지연(High Latency) 대응 절차'에 따라 커넥션 풀 사용률이 90%를 넘으면 풀 크기를 늘리거나 느린 쿼리/외부 호출에 타임아웃을 건다. 지연이 특정 리전/인스턴스에 몰려 있으면 해당 인스턴스를 로드밸런서에서 일시 제외한다. 3) 정상화 확인 후 원인 분석과 재발 방지 대책을 문서화하세요.
```

### q9: 안녕, 너는 누구야?

```
안녕하세요! 무엇을 도와드릴까요?
```

### q10: 제 이름은 민수입니다 → 제 이름이 뭐였죠?

```
민수님이라고 말씀하셨어요. 무엇을 도와드릴까요?
```

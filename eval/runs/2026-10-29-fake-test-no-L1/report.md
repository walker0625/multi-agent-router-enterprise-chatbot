# 라우팅 평가 리포트

- run-id: `2026-10-29-fake-test-no-L1`
- 골드셋: `gold@1#b22297d20f6d`
- 분할: `test (95문장)`
- LLM: `fake-chat`
- 임베더: `hashing-512`
- 뺀 레이어: `L1`
- 판정 재료: `anchors@1#1046cb706aa1, thresholds@2#b423e780da24, judge@1#57e7342761e2`

> fake LLM·해싱 임베더로 돈 결과는 **파이프라인이 설계대로 동작하는지** 보여 줄 뿐 모델 성능이 아니다.
> 실제 모델 수치는 `LLM_PROVIDER=openai` 로 다시 돌려 얻는다. 목표치(예: 정밀도 0.95)와 측정치는 다른 값이다.

## 최종 정확도

전체 70.5% (67/95)

| 정답 라벨 | 정확도 |
|---|---|
| delivery | 80.0% (20/25) |
| sales | 76.0% (19/25) |
| general | 46.7% (7/15) |
| parallel | 46.7% (7/15) |
| hitl | 93.3% (14/15) |

## 레이어별

| 레이어 | 도달 | 결정(결정률) | 정밀도 | 건너뜀 |
|---|---|---|---|---|
| L1 | 0 | - | - | 0 |
| L2 | 95 | 0.0% (0/95) | - | 0 |
| L3 | 95 | 100.0% (95/95) | 70.5% (67/95) | 0 |

어느 레이어도 결정하지 못해 HITL 로 간 질문: 0건

## 되묻기(HITL)

- 정밀도(되물은 것 중 되물어야 했던 것): 41.2% (14/34)
- 재현율(되물어야 했던 것 중 되물은 것): 93.3% (14/15)
- 불필요한 되묻기: 20건

## 혼동 행렬 (행=정답, 열=예측)

| 정답 \ 예측 | delivery | sales | general | parallel | hitl | blocked |
|---|---|---|---|---|---|---|
| delivery | 20 | 0 | 0 | 0 | 5 | 0 |
| sales | 1 | 19 | 0 | 0 | 5 | 0 |
| general | 0 | 0 | 7 | 0 | 8 | 0 |
| parallel | 5 | 1 | 0 | 7 | 2 | 0 |
| hitl | 0 | 1 | 0 | 0 | 14 | 0 |

## 비용·지연

- 라우팅 토큰 합계: 입력 139,953 / 출력 10,770
- 라우팅 지연 중앙값 0ms, 최대 0ms

## 틀린 질문

| id | 질문 | 정답 | 예측 | 결정 |
|---|---|---|---|---|
| del-008 | 컷오버 일정 확정됐어? | delivery | hitl | L3 |
| del-014 | 운임 정산 쪽 이슈 담당자 누구로 정해졌어? | delivery | hitl | L3 |
| del-016 | 데이터 레이크 배치 작업 시간이 겹친 문제 해결됐어? | delivery | hitl | L3 |
| del-024 | 교육 이력 기능 범위에 넣기로 합의했어? | delivery | hitl | L3 |
| del-026 | WBS 기준으로 지금 어느 단계야 | delivery | hitl | L3 |
| sal-006 | 온누리제약 경영기획실 상무 이름 알려줘 | sales | hitl | L3 |
| sal-014 | 한빛유통 IT전략팀 부장 연락처 알려줘 | sales | hitl | L3 |
| sal-018 | 온누리제약 착수 목표일 언제로 협의했어 | sales | hitl | L3 |
| sal-036 | 세움모터스 직원 규모가 몇 명이야 | sales | hitl | L3 |
| sal-040 | 온누리제약 문서 결재 지연 문제를 언급한 고객 측 사람 누구야 | sales | delivery | L3 |
| sal-044 | 다온마트 상무가 말한 목표 수치 | sales | hitl | L3 |
| gen-002 | 인용 번호 누르면 어디로 가? | general | hitl | L3 |
| gen-006 | 답변을 표로 받고 싶은데 설정 방법 | general | hitl | L3 |
| gen-014 | 질문을 되묻는 이유가 뭐야 | general | hitl | L3 |
| gen-016 | 지금 어떤 사용자로 로그인했는지 확인하려면 | general | hitl | L3 |
| gen-018 | 파이썬에서 리스트 정렬하는 법 | general | hitl | L3 |
| gen-020 | 연말정산 서류 언제 내? | general | hitl | L3 |
| gen-024 | 엑셀 피벗테이블 만드는 법 | general | hitl | L3 |
| gen-030 | 업무용 이메일 서명 양식 있어? | general | hitl | L3 |
| par-004 | 미래캐피탈 제안 금액이랑 여신 심사 자동화 기성 청구액 비교해줘 | parallel | hitl | L3 |
| par-008 | 한빛유통이 니즈 파악 때 말한 출고 지연 문제가 WMS 프로젝트에서 해결됐는지 봐줘 | parallel | delivery | L3 |
| par-010 | 대성정밀 하자보수 조건이랑 지금 MES 장애 보고 내용 함께 | parallel | delivery | L3 |
| par-012 | 온누리제약 계약 때 정한 착수 목표일과 실제 프로젝트 시작일 비교 | parallel | hitl | L3 |
| par-016 | 대성정밀 제안 때 약속한 구축 범위와 현재 개발 진척 비교해줘 | parallel | delivery | L3 |
| par-022 | 대성정밀 기술 검토 회의 내용과 설비 모니터링 테스트 결과도 | parallel | delivery | L3 |
| par-024 | 온누리제약 지체상금 협의 결과와 현재 일정 지연 리스크를 같이 봐줘 | parallel | delivery | L3 |
| par-026 | 한빛유통 영업부터 수행까지 전체 히스토리 정리해줘 | parallel | sales | L3 |
| hit-004 | 담당자 누구야? | hitl | sales | L3 |

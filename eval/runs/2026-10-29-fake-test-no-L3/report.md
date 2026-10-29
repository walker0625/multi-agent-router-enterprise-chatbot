# 라우팅 평가 리포트

- run-id: `2026-10-29-fake-test-no-L3`
- 골드셋: `gold@1#b22297d20f6d`
- 분할: `test (95문장)`
- LLM: `fake-chat`
- 임베더: `hashing-512`
- 뺀 레이어: `L3`
- 판정 재료: `keywords@2#bf06a1aeedf1, anchors@1#1046cb706aa1, thresholds@2#b423e780da24`

> fake LLM·해싱 임베더로 돈 결과는 **파이프라인이 설계대로 동작하는지** 보여 줄 뿐 모델 성능이 아니다.
> 실제 모델 수치는 `LLM_PROVIDER=openai` 로 다시 돌려 얻는다. 목표치(예: 정밀도 0.95)와 측정치는 다른 값이다.

## 최종 정확도

전체 48.4% (46/95)

| 정답 라벨 | 정확도 |
|---|---|
| delivery | 76.0% (19/25) |
| sales | 40.0% (10/25) |
| general | 13.3% (2/15) |
| parallel | 0.0% (0/15) |
| hitl | 100.0% (15/15) |

## 레이어별

| 레이어 | 도달 | 결정(결정률) | 정밀도 | 건너뜀 |
|---|---|---|---|---|
| L1 | 95 | 34.7% (33/95) | 93.9% (31/33) | 0 |
| L2 | 62 | 0.0% (0/62) | - | 0 |
| L3 | 0 | - | - | 0 |

어느 레이어도 결정하지 못해 HITL 로 간 질문: 62건

## 되묻기(HITL)

- 정밀도(되물은 것 중 되물어야 했던 것): 24.2% (15/62)
- 재현율(되물어야 했던 것 중 되물은 것): 100.0% (15/15)
- 불필요한 되묻기: 47건

## 혼동 행렬 (행=정답, 열=예측)

| 정답 \ 예측 | delivery | sales | general | parallel | hitl | blocked |
|---|---|---|---|---|---|---|
| delivery | 19 | 0 | 0 | 0 | 6 | 0 |
| sales | 0 | 10 | 0 | 0 | 15 | 0 |
| general | 0 | 0 | 2 | 0 | 13 | 0 |
| parallel | 1 | 1 | 0 | 0 | 13 | 0 |
| hitl | 0 | 0 | 0 | 0 | 15 | 0 |

## 비용·지연

- 라우팅 토큰 합계: 입력 0 / 출력 0
- 라우팅 지연 중앙값 0ms, 최대 0ms

## 틀린 질문

| id | 질문 | 정답 | 예측 | 결정 |
|---|---|---|---|---|
| del-004 | 온누리제약 품질문서 전환 쪽 문서 결재 모듈 개발 끝났어? | delivery | hitl | cascade |
| del-012 | 개발자 추가 투입 비용은 어디서 처리하기로 했어? | delivery | hitl | cascade |
| del-018 | 입고 모듈 요구사항 바뀐 것 때문에 일정 얼마나 밀려? | delivery | hitl | cascade |
| del-034 | 고객 검수 참석자 확정됐어? | delivery | hitl | cascade |
| del-036 | 다온마트 POS 수집 모듈 개발 상황 | delivery | hitl | cascade |
| del-042 | 테스트 데이터 준비가 늦어진 건 해결됐는지 | delivery | hitl | cascade |
| sal-002 | 푸른항운 담당 영업사원 누구지 | sales | hitl | cascade |
| sal-006 | 온누리제약 경영기획실 상무 이름 알려줘 | sales | hitl | cascade |
| sal-010 | 영업2팀 담당 고객사 현황 정리해줘 | sales | hitl | cascade |
| sal-014 | 한빛유통 IT전략팀 부장 연락처 알려줘 | sales | hitl | cascade |
| sal-018 | 온누리제약 착수 목표일 언제로 협의했어 | sales | hitl | cascade |
| sal-020 | 다온마트 제안 차별점 정리해줘 | sales | hitl | cascade |
| sal-024 | 한빛유통 영업 히스토리 처음부터 정리해줘 | sales | hitl | cascade |
| sal-026 | 세움모터스에 낸 제안 요약 첨부 보여줘 | sales | hitl | cascade |
| sal-028 | 에이펙스소프트가 경쟁으로 들어온 건 어디야 | sales | hitl | cascade |
| sal-032 | 다온마트 계약 형태는 구축이랑 유지보수 몇 년으로 했어 | sales | hitl | cascade |
| sal-036 | 세움모터스 직원 규모가 몇 명이야 | sales | hitl | cascade |
| sal-040 | 온누리제약 문서 결재 지연 문제를 언급한 고객 측 사람 누구야 | sales | hitl | cascade |
| sal-044 | 다온마트 상무가 말한 목표 수치 | sales | hitl | cascade |
| sal-046 | 푸른항운 협상 회의 내용 요약 | sales | hitl | cascade |
| sal-050 | 고객사별 영업 담당자 표로 정리해줘 | sales | hitl | cascade |
| gen-002 | 인용 번호 누르면 어디로 가? | general | hitl | cascade |
| gen-004 | 생성 중인 답변 멈추는 버튼 어딨어 | general | hitl | cascade |
| gen-006 | 답변을 표로 받고 싶은데 설정 방법 | general | hitl | cascade |
| gen-008 | 출장 다녀왔는데 경비 신청 기한이 언제야 | general | hitl | cascade |
| gen-014 | 질문을 되묻는 이유가 뭐야 | general | hitl | cascade |
| gen-016 | 지금 어떤 사용자로 로그인했는지 확인하려면 | general | hitl | cascade |
| gen-018 | 파이썬에서 리스트 정렬하는 법 | general | hitl | cascade |
| gen-020 | 연말정산 서류 언제 내? | general | hitl | cascade |
| gen-022 | 휴가 신청은 어디서 해 | general | hitl | cascade |
| gen-024 | 엑셀 피벗테이블 만드는 법 | general | hitl | cascade |
| gen-026 | 대화 지우면 복구돼? | general | hitl | cascade |
| gen-028 | 새 대화 시작하면 이전 맥락 이어져? | general | hitl | cascade |
| gen-030 | 업무용 이메일 서명 양식 있어? | general | hitl | cascade |
| par-002 | 새솔물류 영업 단계와 배차 시스템 구축 공정률을 함께 알려줘 | parallel | hitl | cascade |
| par-004 | 미래캐피탈 제안 금액이랑 여신 심사 자동화 기성 청구액 비교해줘 | parallel | delivery | L1 |
| par-006 | 다온마트 견적과 판매 데이터 플랫폼 진행 상황 같이 보여줘 | parallel | hitl | cascade |
| par-008 | 한빛유통이 니즈 파악 때 말한 출고 지연 문제가 WMS 프로젝트에서 해결됐는지 봐줘 | parallel | sales | L1 |
| par-010 | 대성정밀 하자보수 조건이랑 지금 MES 장애 보고 내용 함께 | parallel | hitl | cascade |
| par-012 | 온누리제약 계약 때 정한 착수 목표일과 실제 프로젝트 시작일 비교 | parallel | hitl | cascade |
| par-014 | 한빛유통 수정 견적 금액과 지금까지 기성 청구한 금액 같이 알려줘 | parallel | hitl | cascade |
| par-016 | 대성정밀 제안 때 약속한 구축 범위와 현재 개발 진척 비교해줘 | parallel | hitl | cascade |
| par-018 | 온누리제약 영업 단계와 품질문서 프로젝트 이슈를 동시에 알려줘 | parallel | hitl | cascade |
| par-020 | 한빛유통 고객 담당자 연락처와 물류센터 프로젝트 장애 이력을 각각 알려줘 | parallel | hitl | cascade |
| par-022 | 대성정밀 기술 검토 회의 내용과 설비 모니터링 테스트 결과도 | parallel | hitl | cascade |
| par-024 | 온누리제약 지체상금 협의 결과와 현재 일정 지연 리스크를 같이 봐줘 | parallel | hitl | cascade |
| par-026 | 한빛유통 영업부터 수행까지 전체 히스토리 정리해줘 | parallel | hitl | cascade |
| par-028 | 대성정밀 고객 실무 담당자랑 프로젝트 개발리더 각각 누구야 | parallel | hitl | cascade |
| par-030 | 온누리제약 제안 설명회 반응과 교육 이력 모듈 개발 현황 함께 | parallel | hitl | cascade |

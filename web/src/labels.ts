// 화면 문구. 채널·결과 코드를 사람이 읽는 이름으로 바꾼다.

export const CHANNEL_LABEL: Record<string, string> = {
  delivery: "수행 에이전트",
  sales: "영업 에이전트",
  general: "일반 안내",
  summary: "종합 요약",
};

export const OUTCOME_LABEL: Record<string, string> = {
  SINGLE: "단일",
  PARALLEL: "병렬",
  HITL: "되묻기",
  BLOCKED: "차단",
};

export const LAYER_LABEL: Record<string, string> = {
  L1: "L1 키워드",
  L2: "L2 유사도",
  L3: "L3 LLM 판정",
  forced: "직접 선택",
  replay: "다시 생성",
  guard: "안전 정책",
};

export const STATUS_LABEL: Record<string, string> = {
  truncated: "길이 제한으로 잘림",
  stopped: "중지됨",
  error: "오류",
  disconnected: "연결 끊김",
};

export const SOURCE_LABEL: Record<string, string> = {
  mail: "메일",
  note: "회의 메모",
  attachment: "첨부",
  account: "고객사",
  contact: "담당자",
  meeting: "영업 회의록",
  faq: "FAQ",
  web: "웹",
};

export const channelLabel = (c: string) => CHANNEL_LABEL[c] ?? c;

// 근거 카드 부가 정보. 내부 ID 는 이름이 함께 오므로 숨긴다.
const META_LABEL: Record<string, string> = {
  project_name: "프로젝트",
  industry: "업종",
  status: "영업 단계",
  kam: "담당 KAM",
  unit: "영업 단위",
  role: "역할",
  phone: "전화",
  email: "메일",
  kind: "회의 종류",
  owner: "작성자",
  confidential: "비공개 회의",
};
const HIDDEN_META = new Set(["project_id", "account_id", "meeting_id"]);

export function metaEntries(meta: Record<string, string>): [string, string][] {
  return Object.entries(meta)
    .filter(([k, v]) => !HIDDEN_META.has(k) && v !== "")
    .map(([k, v]) => [META_LABEL[k] ?? k, v === "true" ? "예" : v === "false" ? "아니오" : v]);
}

// 라우터 REST 응답 모양. 스트림 이벤트 타입은 contract.ts(생성)에 있다.
import type { DoneEvent, TurnStatus } from "./contract";

export interface DemoUser {
  sub: string;
  name: string;
  team: string;
  title: string;
  description: string;
  project_ids: string[];
  sales_units: string[];
  can_read_contacts: boolean;
}

export interface LoginResponse {
  token: string;
  user: DemoUser;
}

export interface Project {
  id: string;
  name: string;
}

export interface Me {
  sub: string;
  name: string;
  team: string;
  project_ids: string[];
  sales_units: string[];
  can_read_contacts: boolean;
  projects: Project[];
}

export interface ChatSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface StoredTurn {
  id: string;
  turn_no: number;
  status: TurnStatus;
  query: string;
  payload: DoneEvent | null;
}

export interface ChatDetail extends ChatSummary {
  turns: StoredTurn[];
}

export interface UserSettings {
  system_prompt: string | null;
}

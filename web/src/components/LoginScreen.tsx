// 데모 로그인. 권한 범위가 서로 다른 가상 사용자 중 하나를 고른다.
import { useEffect, useState } from "react";
import { api } from "../api/http";
import type { DemoUser, LoginResponse } from "../api/types";

export function LoginScreen({ onLogin }: { onLogin: (res: LoginResponse) => void }) {
  const [users, setUsers] = useState<DemoUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<DemoUser[]>("/v1/auth/demo-users")
      .then(setUsers)
      .catch((e: Error) => setError(e.message));
  }, []);

  const login = async (sub: string) => {
    try {
      onLogin(await api<LoginResponse>("/v1/auth/demo-login", { body: { user_id: sub } }));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <main className="login">
      <h1>누리솔루션 업무 어시스턴트</h1>
      <p className="muted">
        가상 회사의 데모입니다. 사용자마다 볼 수 있는 자료 범위가 다릅니다 — 같은 질문을 다른 사용자로 물어보세요.
      </p>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      <div className="user-grid">
        {users?.map((u) => (
          <button key={u.sub} type="button" className="user-card" onClick={() => login(u.sub)} data-user={u.sub}>
            <strong>
              {u.name} <span className="muted">{u.title}</span>
            </strong>
            <span className="muted">{u.team}</span>
            <span>{u.description}</span>
            <span className="tags">
              <span className="badge">프로젝트 {u.project_ids.length}개</span>
              <span className="badge">비공개 영업 {u.sales_units.length ? u.sales_units.join("·") : "없음"}</span>
              <span className="badge">연락처 {u.can_read_contacts ? "열람" : "가림"}</span>
            </span>
          </button>
        ))}
      </div>
    </main>
  );
}

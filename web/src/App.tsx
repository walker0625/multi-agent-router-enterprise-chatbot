// 로그인 상태만 들고 있다. 토큰은 탭 단위(sessionStorage)로 기억하고, 저장소를 못 쓰면 메모리로만 둔다.
import { useState } from "react";
import type { LoginResponse } from "./api/types";
import { LoginScreen } from "./components/LoginScreen";
import { Workspace } from "./components/Workspace";

const KEY = "nuri.session";

function load(): LoginResponse | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as LoginResponse) : null;
  } catch {
    return null;
  }
}

function save(value: LoginResponse | null) {
  try {
    if (value) sessionStorage.setItem(KEY, JSON.stringify(value));
    else sessionStorage.removeItem(KEY);
  } catch {
    // 저장소를 못 쓰는 환경(사생활 보호 모드 등)에서는 새로고침하면 다시 로그인한다.
  }
}

export function App() {
  const [session, setSession] = useState<LoginResponse | null>(load);
  const update = (value: LoginResponse | null) => {
    save(value);
    setSession(value);
  };
  return session ? (
    <Workspace key={session.user.sub} session={session} onLogout={() => update(null)} />
  ) : (
    <LoginScreen onLogin={update} />
  );
}

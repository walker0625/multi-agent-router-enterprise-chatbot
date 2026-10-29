import { defineConfig, devices } from "@playwright/test";

// 전체 스택(라우터·두 에이전트·웹)을 fake LLM 으로 띄워 브라우저에서 끝까지 확인한다.
// 답변이 조금 천천히 흐르게 해서 중지 시나리오가 끼어들 틈을 만든다.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 20_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    locale: "ko-KR",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: "LLM_FAKE_CHUNK_DELAY_S=0.04 uv run python ../scripts/dev.py --fresh",
    url: "http://localhost:5173",
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "ignore",
  },
});

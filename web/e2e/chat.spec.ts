import { type Page, expect, test } from "@playwright/test";

async function login(page: Page, sub: string) {
  await page.goto("/");
  await page.locator(`[data-user="${sub}"]`).click();
  await expect(page.getByLabel("질문 입력")).toBeVisible();
}

async function ask(page: Page, query: string) {
  await page.getByLabel("질문 입력").fill(query);
  await page.getByLabel("질문 입력").press("Enter");
}

const lastTurn = (page: Page) => page.getByTestId("turn").last();

test("단일 질문은 키워드 단계에서 수행 에이전트로 가고, 인용 번호가 근거 카드와 이어진다", async ({ page }) => {
  await login(page, "u-02");
  await ask(page, "P-101 일정 지연 현황 알려줘");
  const turn = lastTurn(page);
  await expect(turn).toHaveAttribute("data-status", "done");
  await expect(turn.getByTestId("route")).toHaveAttribute("data-outcome", "SINGLE");
  await expect(turn.getByTestId("route")).toContainText("L1 키워드");
  await expect(turn.getByTestId("channel-delivery")).toContainText("지연");

  await turn.locator("button.cite").first().click();
  const panel = page.getByTestId("reference-panel");
  await expect(panel).toBeVisible();
  await expect(panel.locator(".ref.active")).toHaveCount(1);
});

test("두 영역에 걸친 질문은 두 에이전트가 함께 답하고 요약이 붙는다", async ({ page }) => {
  await login(page, "u-02");
  await ask(page, "새솔물류 견적 협상 내용과 배차 시스템 프로젝트 진행 상황을 같이 알려줘");
  const turn = lastTurn(page);
  await expect(turn).toHaveAttribute("data-status", "done");
  await expect(turn.getByTestId("route")).toHaveAttribute("data-outcome", "PARALLEL");
  for (const channel of ["delivery", "sales", "summary"]) {
    await expect(turn.getByTestId(`channel-${channel}`)).toBeVisible();
  }
});

test("모호한 질문은 되묻고, 영역 버튼을 고르면 그 영역으로 바로 답한다", async ({ page }) => {
  await login(page, "u-02");
  await ask(page, "그거 어떻게 됐어?");
  const hitl = lastTurn(page).getByTestId("hitl");
  await expect(hitl).toBeVisible();
  await hitl.getByRole("button", { name: /프로젝트 수행/ }).click();

  const answered = lastTurn(page);
  await expect(answered).toHaveAttribute("data-status", "done");
  await expect(answered.getByTestId("route")).toContainText("직접 선택");
  await expect(answered.getByTestId("channel-delivery")).toBeVisible();
});

test("답변 중 중지를 누르면 턴이 중지됨으로 끝난다", async ({ page }) => {
  await login(page, "u-02");
  await ask(page, "P-101 일정 지연 현황 알려줘");
  const turn = lastTurn(page);
  await expect(turn.getByTestId("channel-delivery").locator(".markdown")).toBeVisible();
  await page.getByRole("button", { name: "중지" }).click();
  await expect(turn).toHaveAttribute("data-status", "stopped");
  await expect(page.getByRole("button", { name: "보내기" })).toBeVisible();
});

test("연락처는 권한자에게만 원문으로 보이고, 저장된 대화에서는 가려진다", async ({ page }) => {
  const query = "한빛유통 고객 담당자 연락처 알려줘";
  await login(page, "u-01");
  await ask(page, query);
  const sales = lastTurn(page).getByTestId("channel-sales");
  await expect(lastTurn(page)).toHaveAttribute("data-status", "done");
  await expect(sales).toContainText("010-0000-");

  // 다시 열면 저장본이 보인다 — 저장본에는 연락처 원문이 없다
  await page.getByRole("button", { name: "+ 새 대화" }).click();
  await page.locator(".chat-link").first().click();
  await expect(lastTurn(page).getByTestId("channel-sales")).toContainText("[전화번호]");
  await expect(lastTurn(page).getByTestId("channel-sales")).not.toContainText("010-0000-");

  await page.getByRole("button", { name: "사용자 바꾸기" }).click();
  await login(page, "u-03");
  await ask(page, query);
  await expect(lastTurn(page)).toHaveAttribute("data-status", "done");
  await expect(lastTurn(page).getByTestId("channel-sales")).toContainText("[비공개]");
  await expect(lastTurn(page).getByTestId("channel-sales")).not.toContainText("010-0000-");
});

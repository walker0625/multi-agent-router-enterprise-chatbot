// README 용 화면 캡처. 평소 e2e 에서는 건너뛰고 SCREENSHOTS=1 일 때만 돈다.
// 대화 목록이 깨끗하도록 이 스펙만 따로 돌린다(서버는 매번 빈 저장소로 뜬다).
//   SCREENSHOTS=1 npx playwright test screenshots
import { type Page, expect, test } from "@playwright/test";

const OUT = "../docs/images";

test.skip(!process.env.SCREENSHOTS, "SCREENSHOTS=1 일 때만 캡처한다");

async function login(page: Page, sub: string) {
  await page.goto("/");
  await page.locator(`[data-user="${sub}"]`).click();
  await expect(page.getByLabel("질문 입력")).toBeVisible();
}

async function ask(page: Page, query: string) {
  await page.getByLabel("질문 입력").fill(query);
  await page.getByLabel("질문 입력").press("Enter");
  await expect(page.getByTestId("turn").last()).not.toHaveAttribute("data-status", "streaming");
}

test("화면 캡처", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("[data-user]").first()).toBeVisible();
  await page.screenshot({ path: `${OUT}/login.png` });

  await login(page, "u-02");
  await ask(page, "P-101 일정 지연 현황 알려줘");
  await page.getByTestId("turn").last().locator("button.cite").first().click();
  await expect(page.getByTestId("reference-panel")).toBeVisible();
  await page.screenshot({ path: `${OUT}/single-with-references.png` });

  await page.getByRole("button", { name: "+ 새 대화" }).click();
  await ask(page, "그거 어떻게 됐어?");
  await page.screenshot({ path: `${OUT}/hitl.png` });

  // 병렬: 비공개 가격 협상 회의록과 P-101 을 모두 볼 수 있는 영업 담당자로. 답변이 길어 화면을 늘린다.
  await page.getByRole("button", { name: "사용자 바꾸기" }).click();
  await login(page, "u-01");
  await page.setViewportSize({ width: 1440, height: 1400 });
  await ask(page, "한빛유통 가격 협상 내용과 물류센터 WMS 프로젝트 진행 상황을 같이 알려줘");
  await page.screenshot({ path: `${OUT}/parallel.png` });
});

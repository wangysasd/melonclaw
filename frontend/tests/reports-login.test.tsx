import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { LoginPage } from "../src/components/LoginPage";
import { MyReports } from "../src/components/MyReports";
import * as auth from "../src/api/auth";
import * as reports from "../src/api/reports";

vi.mock("../src/api/auth", () => ({
  getAuthConfig: vi.fn(), login: vi.fn(), passwordlessLogin: vi.fn(), getAuthSession: vi.fn(), setRequestUser: vi.fn(),
  loginWithSms: vi.fn(), registerAccount: vi.fn(), sendSmsChallenge: vi.fn(),
  AUTH_EVENT: "melonclaw-auth-change", AUTH_STORAGE: "melonclaw.auth-change",
}));
vi.mock("../src/api/reports", async (load) => ({
  ...await load<typeof import("../src/api/reports")>(),
  reportChannels: vi.fn(),
  submitAnonymousReport: vi.fn(),
  lookupReport: vi.fn(),
  submitReport: vi.fn(),
  listMyReports: vi.fn(),
  getMyReport: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(auth.getAuthConfig).mockResolvedValue({ passwordless: false, assistant_name: "Melon" });
  Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) });
});

it("opens the complaint page without inventing a contact channel", async () => {
  vi.mocked(reports.reportChannels).mockResolvedValue({});
  render(<LoginPage />);
  fireEvent.click(await screen.findByRole("button", { name: "意见反馈" }));
  expect(await screen.findByRole("heading", { name: "意见反馈" })).toBeTruthy();
  expect(screen.getByText(/未配置对外联系渠道/)).toBeTruthy();
  expect(screen.queryByText(/@/)).toBeNull();
  expect(screen.getByRole("button", { name: "返回登录" })).toBeTruthy();
});

it("shows only a configured email and response commitment", async () => {
  vi.mocked(reports.reportChannels).mockResolvedValue({
    email: "ops@example.com", contact_name: "运营值班", response_hours: 48,
  });
  render(<LoginPage rms />);
  fireEvent.click(screen.getByRole("button", { name: "意见反馈" }));
  expect(await screen.findByRole("link", { name: "ops@example.com" })).toBeTruthy();
  expect(screen.getByText("联系人：运营值班")).toBeTruthy();
  expect(screen.getByText(/48 小时内响应/)).toBeTruthy();
});

const generalFeedback: reports.ReportDetail = {
  public_code: "MC-1234ABCD",
  kind: "general",
  reason: "illegal",
  status: "resolved",
  target: { kind: "general", label: "一般投诉" },
  description: "共享文档中发现违规内容",
  contact: null,
  has_feedback: true,
  public_feedback: "已核查并处理",
  created_at: "2026-11-28T00:00:00Z",
  updated_at: "2026-11-28T01:00:00Z",
  events: [],
};

async function submitContentFeedback(form: ReturnType<typeof within>) {
  fireEvent.mouseDown(form.getByRole("combobox", { name: "反馈原因" }));
  fireEvent.click(await screen.findByRole("option", { name: "违法不良信息" }));
  fireEvent.change(form.getByRole("textbox", { name: "反馈说明" }), {
    target: { value: generalFeedback.description },
  });
  fireEvent.click(form.getByRole("button", { name: "提交反馈" }));
}

it("lets a visitor submit content feedback and look up the result using a code", async () => {
  vi.mocked(reports.reportChannels).mockResolvedValue({});
  vi.mocked(reports.submitAnonymousReport).mockResolvedValue({
    public_code: generalFeedback.public_code, lookup_code: "visitor-test-code", status: "pending", notice: "请保存编号和查询码",
  });
  vi.mocked(reports.lookupReport).mockResolvedValue(generalFeedback);
  render(<LoginPage />);
  fireEvent.click(await screen.findByRole("button", { name: "意见反馈" }));
  await submitContentFeedback(within(document.body));

  expect((await screen.findByRole("region", { name: "提交凭据" })).textContent).toContain("visitor-test-code");
  expect(reports.submitAnonymousReport).toHaveBeenCalledWith(expect.objectContaining({
    reason: "illegal", description: generalFeedback.description,
  }));
  fireEvent.change(screen.getByLabelText("工单编号"), { target: { value: generalFeedback.public_code } });
  fireEvent.change(screen.getByLabelText("查询码"), { target: { value: "visitor-test-code" } });
  fireEvent.click(screen.getByRole("button", { name: /^查\s*询$/ }));
  expect(await screen.findByText(/已核查并处理/)).toBeTruthy();
});

it("lets a signed-in user submit the same form and open their own receipt", async () => {
  vi.mocked(reports.listMyReports).mockResolvedValue({ items: [generalFeedback] });
  vi.mocked(reports.submitReport).mockResolvedValue({
    public_code: generalFeedback.public_code, status: "pending", notice: "已提交",
  });
  vi.mocked(reports.getMyReport).mockResolvedValue(generalFeedback);
  render(<MyReports onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));
  await submitContentFeedback(within(await screen.findByRole("dialog", { name: "提交反馈" })));

  expect(await screen.findByRole("dialog", { name: "反馈已提交" })).toBeTruthy();
  expect(reports.submitReport).toHaveBeenCalledWith(expect.objectContaining({
    reason: "illegal", description: generalFeedback.description, target: { kind: "general" },
  }));
  fireEvent.click(screen.getByRole("button", { name: "查看进度" }));
  await waitFor(() => expect(reports.getMyReport).toHaveBeenCalledWith(generalFeedback.public_code));
  expect(await screen.findByText("已核查并处理")).toBeTruthy();
});

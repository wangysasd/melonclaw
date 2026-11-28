import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { BindPhoneDialog } from "../src/components/BindPhoneDialog";
import { GlobalNav } from "../src/components/GlobalNav";
import { MyReports } from "../src/components/MyReports";
import { PrivacyPage } from "../src/components/PrivacyPage";
import { getAuthConfig, sendBindChallenge } from "../src/api/auth";
import { getMyReport, listMyReports, type ReportSummary } from "../src/api/reports";
import { publicOperatorEmail } from "../src/lib/privacyMail";

vi.mock("../src/api/auth", () => ({
  getAuthConfig: vi.fn(),
  getAuthSession: vi.fn(),
  logout: vi.fn(),
  sendBindChallenge: vi.fn(),
  bindPhone: vi.fn(),
}));
vi.mock("../src/api/reports", async (importOriginal) => ({
  ...await importOriginal<typeof import("../src/api/reports")>(),
  listMyReports: vi.fn(),
  getMyReport: vi.fn(),
}));
vi.mock("../src/state/session", () => ({
  useSession: () => ({
    userId: "alice",
    users: [{ user_id: "alice", display_name: "Alice" }],
    status: { status: "ready" },
    modelOptions: [],
  }),
}));

const contact = {
  passwordless: false,
  assistant_name: "Melon",
  registration: false,
  sms_login: true,
  operator_name: "测试主体",
  operator_email: "privacy-contact@example.test",
};

beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
  vi.mocked(getAuthConfig).mockResolvedValue(contact);
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(),
    })),
  });
});

it("shows the configured contact email as a mail link", async () => {
  expect(publicOperatorEmail("not-an-email")).toBe("");
  expect(publicOperatorEmail("")).toBe("");
  render(<PrivacyPage />);
  expect(await screen.findByText(/本服务由测试主体运营/)).toBeTruthy();
  const link = await screen.findByRole("link", { name: "privacy-contact@example.test" });
  expect(link.getAttribute("href")).toBe("mailto:privacy-contact@example.test");
  expect(screen.queryByText(/已提交|已受理/)).toBeNull();
});

it("stays readable and does not invent a mail link when contact is missing", async () => {
  vi.mocked(getAuthConfig).mockResolvedValue({
    passwordless: false, assistant_name: "Melon", registration: false, sms_login: false,
    operator_name: "", operator_email: "",
  });
  render(<PrivacyPage />);
  expect(await screen.findByText(/暂未提供个人信息申请邮箱/)).toBeTruthy();
  expect(screen.queryByRole("link", { name: "写邮件申请" })).toBeNull();
  expect(screen.queryByLabelText("运营邮箱")).toBeNull();
  expect(document.body.innerHTML).not.toContain("mailto:");
  expect(screen.queryByText(/已提交|已受理/)).toBeNull();
});

it("keeps the page available when the contact request fails", async () => {
  vi.mocked(getAuthConfig).mockRejectedValue(new Error("offline"));
  render(<PrivacyPage />);
  expect(await screen.findByText(/联系方式暂时无法获取/)).toBeTruthy();
  expect(screen.getByText(/不保存完整手机号或短信验证码/)).toBeTruthy();
  expect(document.body.innerHTML).not.toContain("mailto:");
});

it("opens the feedback workspace from the account menu", async () => {
  const openFeedback = vi.fn();
  vi.mocked(getAuthConfig).mockResolvedValue({ ...contact, sms_login: false });
  render(<App><GlobalNav view="chat" onHome={() => {}} onResources={() => {}} onAmp={() => {}} onMindera={() => {}} onFeedback={openFeedback} /></App>);
  fireEvent.click(screen.getByRole("button", { name: /当前用户/ }));
  expect(screen.queryByText("个人信息申请")).toBeNull();
  fireEvent.click(await screen.findByText("意见反馈"));
  await waitFor(() => expect(openFeedback).toHaveBeenCalledOnce());
});

it("links from my reports to the public request section", async () => {
  vi.mocked(listMyReports).mockResolvedValue({ items: [] });
  render(<App><MyReports onClose={vi.fn()} /></App>);
  await waitFor(() => expect(listMyReports).toHaveBeenCalled());
  expect(screen.getByRole("heading", { name: "意见反馈" })).toBeTruthy();
  const link = screen.getByRole("link", { name: "个人信息申请" });
  expect(link.getAttribute("href")).toBe("/privacy#requests");
});

it("filters recent reports as the user types and queries an older report on Enter", async () => {
  const report: ReportSummary = {
    public_code: "MC-ABCD1234",
    status: "pending",
    kind: "general",
    reason: "other",
    target: { kind: "general", label: "一般投诉" },
    has_feedback: false,
    updated_at: "2026-11-27T08:00:00Z",
    created_at: "2026-11-27T08:00:00Z",
  };
  const olderCode = "MC-00000001";
  vi.mocked(listMyReports).mockResolvedValue({ items: [report] });
  vi.mocked(getMyReport).mockResolvedValue({
    ...report, public_code: olderCode, description: "较早的工单", contact: null, public_feedback: null, events: [],
  });
  render(<App><MyReports onClose={vi.fn()} /></App>);
  await screen.findByText(report.public_code);
  const search = screen.getByRole("textbox", { name: "搜索工单编号" });
  fireEvent.change(search, { target: { value: "abcd" } });
  expect(screen.getByText(report.public_code)).toBeTruthy();
  fireEvent.change(search, { target: { value: olderCode.toLowerCase() } });
  expect(screen.queryByText(report.public_code)).toBeNull();
  expect(getMyReport).not.toHaveBeenCalled();
  fireEvent.keyDown(search, { key: "Enter", code: "Enter", keyCode: 13 });
  expect(await screen.findByText("较早的工单")).toBeTruthy();
  expect(getMyReport).toHaveBeenCalledWith(olderCode);
});

it("shows the bind notice without sending a code", () => {
  render(<App><BindPhoneDialog onClose={vi.fn()} onBound={vi.fn()} /></App>);
  expect(screen.getByText(/验证码由阿里云发送并核验/)).toBeTruthy();
  fireEvent.click(screen.getByRole("link", { name: "隐私说明" }));
  expect(sendBindChallenge).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "隐私说明" }).getAttribute("href")).toBe("/privacy");
});

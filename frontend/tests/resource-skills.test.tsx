import { App as AntdApp } from "antd";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ResourceView } from "../src/components/ResourceView";
import { listManageableModels, listManageableProviders, listManageableSkills, updateSkill } from "../src/api/client";
import type { ManageableSkill } from "../src/types/api";

vi.mock("../src/api/client", () => ({
  listManageableSkills: vi.fn(),
  updateSkill: vi.fn(),
  listManageableModels: vi.fn(),
  listManageableProviders: vi.fn(),
}));

const session = {
  contextReady: true,
  status: { status: "ready" },
  userId: "admin-1",
  users: [{ user_id: "admin-1", tenant_role: "admin" }],
  refreshSkills: vi.fn(),
  refreshModels: vi.fn(),
};

vi.mock("../src/state/session", () => ({ useSession: () => session }));

function skill(overrides: Partial<ManageableSkill>): ManageableSkill {
  return {
    name: "s",
    scope: "user",
    source_type: "upload",
    enabled: true,
    user_enabled: null,
    created_by: "admin-1",
    display_name: "技能",
    description: "说明",
    availability: "ready",
    ...overrides,
  };
}

async function renderSkills(items: ManageableSkill[]) {
  vi.mocked(listManageableSkills).mockResolvedValue({ items } as never);
  render(
    <AntdApp>
      <ResourceView onClose={() => {}} onTrySkill={() => {}} />
    </AntdApp>,
  );
  await waitFor(() => expect(screen.getByText(items[0].display_name)).toBeTruthy());
}

/** 卡片主操作按钮（使用/添加），按 aria-label 精确取。 */
function primaryAction(name: string): HTMLButtonElement {
  return screen.getByRole("button", {
    name: new RegExp(`(使用|添加)技能：${name}`),
  }) as HTMLButtonElement;
}

beforeEach(() => {
  vi.mocked(listManageableSkills).mockReset();
  vi.mocked(listManageableModels).mockReset().mockResolvedValue({ items: [] } as never);
  vi.mocked(listManageableProviders).mockReset().mockResolvedValue({ items: [] } as never);
});

describe("resource skills availability", () => {
  it("marks rows the disk cannot serve and disables their controls", async () => {
    await renderSkills([
      skill({ name: "ok", scope: "global", display_name: "正常技能" }),
      skill({
        name: "gone",
        display_name: "丢失技能",
        availability: "missing",
      }),
    ]);

    // 行还在但磁盘上没了：徽章 + 说明写清该做什么。
    expect(screen.getByText("目录已丢失")).toBeTruthy();
    expect(
      screen.getByText("目录已丢失，该技能不可用。删除这一行后可以重新上传。"),
    ).toBeTruthy();

    // 坏行的主操作与发布被禁用；正常行可用。
    expect(primaryAction("丢失技能").disabled).toBe(true);
    expect(primaryAction("正常技能").disabled).toBe(false);
    const publishButtons = screen.getAllByRole("button", {
      name: /^发\s*布$/,
    }) as HTMLButtonElement[];
    expect(publishButtons.map((node) => node.disabled)).toEqual([true]);
  });

  it("tells invalid rows apart from missing ones", async () => {
    await renderSkills([
      skill({ name: "broken", display_name: "坏文件技能", availability: "invalid" }),
    ]);

    expect(screen.getByText("技能文件异常")).toBeTruthy();
    expect(
      screen.getByText("技能文件无法读取，该技能不可用。修复 SKILL.md 后会自动恢复。"),
    ).toBeTruthy();
    expect(screen.queryByText("目录已丢失")).toBeNull();
  });

  it("groups skills by scope for admins and members", async () => {
    // admin 视角：共享技能落在「系统内置」组，不出现「我的」组标题。
    await renderSkills([skill({ name: "ok", scope: "global", display_name: "正常技能" })]);
    expect(screen.getByText("系统内置")).toBeTruthy();
    expect(screen.queryByText("我的")).toBeNull();

    // 成员视角：自己的私有技能落在「我的」组。
    session.userId = "member-1";
    session.users = [{ user_id: "member-1", tenant_role: "member" }];
    try {
      cleanup();
      await renderSkills([skill({ name: "mine", display_name: "我的技能", created_by: "member-1" })]);
      expect(screen.getByText("我的")).toBeTruthy();
      expect(screen.queryByText("系统内置")).toBeNull();
    } finally {
      session.userId = "admin-1";
      session.users = [{ user_id: "admin-1", tenant_role: "admin" }];
    }
  });

  it("leaves ready rows fully editable", async () => {
    await renderSkills([skill({ name: "ok", display_name: "正常技能" })]);

    expect(screen.queryByText("目录已丢失")).toBeNull();
    expect(screen.queryByText("技能文件异常")).toBeNull();
    expect(screen.getByText("说明")).toBeTruthy();
    expect(primaryAction("正常技能").disabled).toBe(false);
    expect(
      (screen.getByRole("button", { name: /^发\s*布$/ }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });
});


it("keeps card order after adding and uninstalling until manual refresh", async () => {
  const alpha = skill({ name: "alpha", display_name: "Alpha", user_enabled: false });
  const beta = skill({ name: "beta", display_name: "Beta" });
  await renderSkills([alpha, beta]);
  const order = () => screen.getAllByRole("button", { name: /^查看技能详情：/ })
    .map((node) => node.getAttribute("aria-label"));
  const initial = order();
  expect(initial).toEqual(["查看技能详情：Beta", "查看技能详情：Alpha"]);
  vi.mocked(updateSkill).mockResolvedValue({ ok: true });
  vi.mocked(listManageableSkills).mockResolvedValue({ items: [{ ...alpha, user_enabled: true }, beta] } as never);
  fireEvent.click(primaryAction("Alpha"));
  await screen.findByRole("button", { name: "使用技能：Alpha" });
  expect(order()).toEqual(initial);
  vi.mocked(listManageableSkills).mockResolvedValue({ items: [{ ...alpha, user_enabled: true }, { ...beta, user_enabled: false }] } as never);
  fireEvent.click(screen.getByRole("button", { name: "更多技能操作：Beta" }));
  fireEvent.click(await screen.findByText("卸载"));
  await screen.findByRole("button", { name: "添加技能：Beta" });
  expect(order()).toEqual(initial);
  fireEvent.click(screen.getByRole("button", { name: "刷新技能列表" }));
  await waitFor(() => expect(order()).toEqual(["查看技能详情：Alpha", "查看技能详情：Beta"]));
});

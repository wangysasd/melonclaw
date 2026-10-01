import { App as AntdApp } from "antd";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ResourceView } from "../src/components/ResourceView";
import {
  listManageableModels,
  listManageableProviders,
  listManageableSkills,
  prepareRemoteSkillInstall,
  skillDetails,
  prepareSkillImport,
  confirmSkillImport,
  updateSkill,
  updateSkillGlobalState,
} from "../src/api/client";
import type { ManageableSkill } from "../src/types/api";

vi.mock("../src/api/client", () => ({
  listManageableSkills: vi.fn(),
  updateSkill: vi.fn(),
  updateSkillGlobalState: vi.fn(),
  listManageableModels: vi.fn(),
  listManageableProviders: vi.fn(),
  prepareRemoteSkillInstall: vi.fn(),
  skillDetails: vi.fn(), prepareSkillImport: vi.fn(), confirmSkillImport: vi.fn(), cancelSkillImport: vi.fn(),
  downloadSkill: vi.fn(), deleteSkill: vi.fn(), recoverSkills: vi.fn(),
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
  const row = {
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
  const personal = row.scope === "global" ? row.user_enabled !== false : row.enabled;
  return {
    id: `${row.scope}:${row.name}`, selection_id: `${row.scope}:${row.name}`,
    version: 1, content_hash: "hash", source_url: "", source_ref: "",
    personally_enabled: personal,
    effective_enabled: personal && row.enabled && row.availability === "ready" && !row.shadowed,
    unavailable_reason: null, diagnostic: "", ...row,
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
  vi.mocked(prepareRemoteSkillInstall).mockReset();
  vi.mocked(updateSkill).mockReset();
  vi.mocked(updateSkillGlobalState).mockReset();
  vi.mocked(skillDetails).mockResolvedValue({
    content_hash: "hash", body: "# 技能正文", body_truncated: false, files: [],
    changes: { added: [], removed: [], modified: [] }, diff: "", dependency_checks: [],
  });
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
      screen.getByText("目录已丢失，可上传更新修复或删除记录。"),
    ).toBeTruthy();

    // 坏行的主操作被禁用；正常行可用。
    expect(primaryAction("丢失技能").disabled).toBe(true);
    expect(primaryAction("正常技能").disabled).toBe(false);
    expect(document.querySelector('[aria-label="查看技能详情：正常技能"] .app-logo')?.textContent).toBe("正");
    expect(document.querySelector('[aria-label="查看技能详情：丢失技能"] .app-logo')?.textContent).toBe("丢");
    expect(screen.queryByRole("button", { name: /^发\s*布$/ })).toBeNull();
  });

  it("tells invalid rows apart from missing ones", async () => {
    await renderSkills([
      skill({ name: "broken", display_name: "坏文件技能", availability: "invalid" }),
    ]);

    expect(screen.getByText("技能文件异常")).toBeTruthy();
    expect(
      screen.getByText("技能文件无法读取，可查看诊断并上传更新修复。"),
    ).toBeTruthy();
    expect(screen.queryByText("目录已丢失")).toBeNull();
  });

  it("groups skills by scope for admins and members", async () => {
    // admin 视角：共享技能落在「系统共享」组，不出现「我的」组标题。
    await renderSkills([skill({ name: "ok", scope: "global", display_name: "正常技能" })]);
    expect(screen.getByText("系统共享")).toBeTruthy();
    expect(screen.queryByText("我的")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "更多技能操作：正常技能" }));
    expect(await screen.findByText("全员停用")).toBeTruthy();

    // 成员视角：自己的私有技能落在「我的」组。
    session.userId = "member-1";
    session.users = [{ user_id: "member-1", tenant_role: "member" }];
    try {
      cleanup();
      await renderSkills([skill({ name: "mine", display_name: "我的技能", created_by: "member-1" })]);
      expect(screen.getByText("我的")).toBeTruthy();
      expect(screen.queryByText("系统共享")).toBeNull();

      cleanup();
      await renderSkills([skill({ name: "shared", scope: "global", display_name: "共享技能" })]);
      fireEvent.click(screen.getByRole("button", { name: "更多技能操作：共享技能" }));
      expect(await screen.findByText("我不使用")).toBeTruthy();
    } finally {
      session.userId = "admin-1";
      session.users = [{ user_id: "admin-1", tenant_role: "admin" }];
    }
  });

  it("lets admins add a disabled shared skill", async () => {
    await renderSkills([
      skill({ name: "active", scope: "global", display_name: "可用共享技能" }),
      skill({
        name: "disabled",
        scope: "global",
        display_name: "全员停用技能",
        enabled: false,
      }),
    ]);

    expect(screen.getByText("可用共享技能")).toBeTruthy();
    expect(screen.getByText("全员停用技能")).toBeTruthy();
    expect(primaryAction("全员停用技能").disabled).toBe(true);
    vi.mocked(updateSkillGlobalState).mockResolvedValue(undefined as never);
    fireEvent.click(screen.getByRole("button", { name: "更多技能操作：全员停用技能" }));
    fireEvent.click(await screen.findByText("全员启用"));
    await waitFor(() =>
      expect(updateSkillGlobalState).toHaveBeenCalledWith("disabled", {
        userId: "admin-1", enabled: true,
      }),
    );
  });

  it("hides a disabled shared skill from members", async () => {
    session.userId = "member-1";
    session.users = [{ user_id: "member-1", tenant_role: "member" }];
    try {
      await renderSkills([
        skill({ name: "active", scope: "global", display_name: "可用共享技能" }),
        skill({ name: "disabled", scope: "global", display_name: "待启用共享技能", enabled: false }),
      ]);
      expect(screen.queryByText("待启用共享技能")).toBeNull();
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
    expect(screen.queryByRole("button", { name: /^发\s*布$/ })).toBeNull();
  });
});

it("aborts a remote installation when cancel is clicked", async () => {
  let receivedSignal: AbortSignal | undefined;
  vi.mocked(prepareRemoteSkillInstall).mockImplementation(
    (_input, signal) =>
      new Promise((_, reject) => {
        receivedSignal = signal;
        signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        });
      }) as never,
  );
  await renderSkills([skill({ name: "ok", display_name: "正常技能" })]);

  fireEvent.click(screen.getByRole("button", { name: /远程安装/ }));
  fireEvent.change(screen.getAllByRole("textbox").at(-1)!, {
    target: { value: "owner/repo" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^安\s*装$/ }));
  await waitFor(() => expect(receivedSignal).toBeDefined());
  expect(screen.getByRole("button", { name: "取消安装" })).toBeTruthy();

  expect(document.querySelector(".remote-skill-modal .ant-modal-close")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "取消安装" }));

  await waitFor(() => expect(receivedSignal?.aborted).toBe(true));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "远程安装 Skill" })).toBeNull());
});


it("keeps card order after adding and uninstalling until manual refresh", async () => {
  const alpha = skill({ name: "alpha", display_name: "Alpha", enabled: false });
  const beta = skill({ name: "beta", display_name: "Beta" });
  await renderSkills([alpha, beta]);
  const order = () => screen.getAllByRole("button", { name: /^查看技能详情：/ })
    .map((node) => node.getAttribute("aria-label"));
  const initial = order();
  expect(initial).toEqual(["查看技能详情：Beta", "查看技能详情：Alpha"]);
  vi.mocked(updateSkill).mockResolvedValue({ ok: true });
  vi.mocked(listManageableSkills).mockResolvedValue({ items: [{ ...alpha, enabled: true, personally_enabled: true, effective_enabled: true }, beta] } as never);
  fireEvent.click(primaryAction("Alpha"));
  await screen.findByRole("button", { name: "使用技能：Alpha" });
  expect(order()).toEqual(initial);
  vi.mocked(listManageableSkills).mockResolvedValue({ items: [{ ...alpha, enabled: true, personally_enabled: true, effective_enabled: true }, { ...beta, enabled: false, personally_enabled: false, effective_enabled: false }] } as never);
  fireEvent.click(screen.getByRole("button", { name: "更多技能操作：Beta" }));
  fireEvent.click(await screen.findByText("我不使用"));
  await screen.findByRole("button", { name: "添加技能：Beta" });
  expect(order()).toEqual(initial);
  fireEvent.click(screen.getByRole("button", { name: "刷新技能列表" }));
  await waitFor(() => expect(order()).toEqual(["查看技能详情：Alpha", "查看技能详情：Beta"]));
});

it("keeps admin personal preference separate from the global switch", async () => {
  await renderSkills([skill({ name: "shared", scope: "global", display_name: "共享操作" })]);
  vi.mocked(updateSkill).mockResolvedValue({ ok: true });
  fireEvent.click(screen.getByRole("button", { name: "更多技能操作：共享操作" }));
  fireEvent.click(await screen.findByText("我不使用"));
  await waitFor(() => expect(updateSkill).toHaveBeenCalledWith("shared", {
    userId: "admin-1", scope: "global", enabled: false,
  }));
  expect(updateSkillGlobalState).not.toHaveBeenCalled();
});

it("keeps same-name shared and private cards distinct after refresh", async () => {
  const shared = skill({ name: "same", scope: "global", display_name: "共享版本" });
  const mine = skill({ name: "same", display_name: "个人版本", enabled: false });
  await renderSkills([shared, mine]);
  fireEvent.click(primaryAction("个人版本"));
  await waitFor(() => expect(updateSkill).toHaveBeenCalledWith("same", {
    userId: "admin-1", scope: "user", enabled: true,
  }));
  expect(screen.getByRole("button", { name: "查看技能详情：共享版本" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "查看技能详情：个人版本" })).toBeTruthy();
});

it("previews update content and file changes before confirming the target", async () => {
  const target = skill({ name: "report", display_name: "报告技能", version: 3 });
  await renderSkills([target]);
  vi.mocked(prepareSkillImport).mockResolvedValue({
    draft_id: "draft-update", name: "report", display_name: "报告技能", description: "新内容",
    scope: "user", operation: "update", target_id: target.id, base_version: 3,
    source_type: "upload", source_url: "", source_ref: "", file_count: 2, expires_at: 9999999999,
    preview: {
      content_hash: "new-hash", body: "# 新版正文", body_truncated: false,
      files: [{ path: "SKILL.md", size: 10, sha256: "hash" }],
      changes: { added: ["references/example.md"], modified: ["SKILL.md"], removed: ["old.py"] },
      diff: "-旧版\n+新版", dependency_checks: [{ kind: "mcp", name: "reports", status: "missing" }],
    },
  });
  vi.mocked(confirmSkillImport).mockResolvedValue({ ok: true });
  fireEvent.click(screen.getByRole("button", { name: "更多技能操作：报告技能" }));
  fireEvent.click(await screen.findByText("上传更新内容"));
  const file = new File(["zip"], "report.zip", { type: "application/zip" });
  fireEvent.change(document.querySelector('input[type="file"]')!, { target: { files: [file] } });
  expect(await screen.findByText("# 新版正文")).toBeTruthy();
  expect(screen.getByText("版本：v3 → v4")).toBeTruthy();
  expect(screen.getByText("删除：old.py")).toBeTruthy();
  expect(screen.getByText("mcp · reports：缺少")).toBeTruthy();
  expect(confirmSkillImport).not.toHaveBeenCalled();
  expect(prepareSkillImport).toHaveBeenCalledWith({ userId: "admin-1", file, targetId: target.id });
  fireEvent.click(screen.getByRole("button", { name: "确认更新" }));
  await waitFor(() => expect(confirmSkillImport).toHaveBeenCalledWith({ userId: "admin-1", draftId: "draft-update" }));
});

it("loads the selected scope's body in details", async () => {
  await renderSkills([skill({ name: "body", display_name: "正文技能" })]);
  fireEvent.click(screen.getByRole("button", { name: "查看技能详情：正文技能" }));
  expect(await screen.findByText("# 技能正文")).toBeTruthy();
  expect(skillDetails).toHaveBeenCalledWith("body", { userId: "admin-1", scope: "user" });
});

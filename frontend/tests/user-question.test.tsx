import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { UserQuestionPanel } from "../src/components/UserQuestionPanel";
import type { UserQuestionItem, UserQuestionRequest } from "../src/types/api";

const singleQuestion: UserQuestionItem = {
  id: "question-1",
  question: "选择部署方式",
  options: [
    { id: "local", label: "本地", description: "当前工作区" },
    { id: "cloud", label: "云端" },
  ],
  allow_custom_answer: true,
  multi_select: false,
};

/** 单题卡片：协议里就是长度为一的 questions 数组。 */
const question: UserQuestionRequest = {
  kind: "user_question",
  schema_version: 2,
  interaction_id: "interaction-1",
  interrupt_id: "interrupt-1",
  assistant_message_id: "assistant-1",
  questions: [singleQuestion],
  expires_at: "2030-01-01T00:00:00+00:00",
};

function withQuestions(items: UserQuestionItem[]): UserQuestionRequest {
  return { ...question, questions: items };
}

function withSingleQuestion(patch: Partial<UserQuestionItem>): UserQuestionRequest {
  return withQuestions([{ ...singleQuestion, ...patch }]);
}

describe("UserQuestionPanel", () => {
  it("requires a choice and submits the trusted option id", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<UserQuestionPanel question={question} onSubmit={onSubmit} />);
    expect(screen.getByRole("region", { name: /需要你补充信息/ })).toBeTruthy();
    expect(screen.getByText("等待回答")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "允许本次" })).toBeNull();
    const submit = screen.getByRole("button", { name: "提交回答并继续" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByLabelText("本地"));
    await user.click(submit);
    expect(onSubmit).toHaveBeenCalledWith({
      type: "batch",
      answers: { "question-1": { type: "option", option_id: "local" } },
    });
  });

  it("supports a custom text answer when enabled", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<UserQuestionPanel question={question} onSubmit={onSubmit} />);
    await user.click(screen.getByLabelText("其他"));
    await user.type(screen.getByLabelText("自定义回答"), "先做演示");
    await user.click(screen.getByRole("button", { name: "提交回答并继续" }));
    expect(onSubmit).toHaveBeenCalledWith({
      type: "batch",
      answers: { "question-1": { type: "text", text: "先做演示" } },
    });
  });

  it("drops the previous answer when the question changes under the same interaction id", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <UserQuestionPanel question={question} onSubmit={onSubmit} />,
    );
    await user.click(screen.getByLabelText("本地"));
    // 账本被复用时 interaction_id 不变，只有题干和选项变了。
    rerender(
      <UserQuestionPanel
        question={withSingleQuestion({
          question: "选择运行环境",
          options: [
            { id: "docker", label: "容器" },
            { id: "host", label: "主机" },
          ],
        })}
        onSubmit={onSubmit}
      />,
    );
    expect((screen.getByLabelText("容器") as HTMLInputElement).checked).toBe(false);
    expect(
      (screen.getByRole("button", { name: "提交回答并继续" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    await user.click(screen.getByLabelText("容器"));
    await user.click(screen.getByRole("button", { name: "提交回答并继续" }));
    expect(onSubmit).toHaveBeenCalledWith({
      type: "batch",
      answers: { "question-1": { type: "option", option_id: "docker" } },
    });
  });

  it("supports selecting several options and adding custom context", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <UserQuestionPanel
        question={withSingleQuestion({ multi_select: true })}
        onSubmit={onSubmit}
      />,
    );
    await user.click(screen.getByLabelText("本地"));
    await user.click(screen.getByLabelText("云端"));
    await user.type(screen.getByLabelText("自定义回答"), "需要灰度发布");
    await user.click(screen.getByRole("button", { name: "提交回答并继续" }));
    expect(onSubmit).toHaveBeenCalledWith({
      type: "batch",
      answers: {
        "question-1": {
          type: "options",
          option_ids: ["local", "cloud"],
          text: "需要灰度发布",
        },
      },
    });
  });

  it("shows multiple questions in one card and submits them together", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <UserQuestionPanel
        question={withQuestions([
          {
            id: "question-1",
            question: "选择部署环境",
            options: singleQuestion.options,
            allow_custom_answer: false,
            multi_select: false,
          },
          {
            id: "question-2",
            question: "补充说明",
            options: [],
            allow_custom_answer: true,
            multi_select: false,
          },
        ])}
        onSubmit={onSubmit}
      />,
    );
    const submit = screen.getByRole("button", { name: "提交全部回答并继续" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByLabelText("本地"));
    await user.type(screen.getByLabelText("第 2 个问题的自定义回答"), "需要灰度发布");
    await user.click(submit);
    expect(onSubmit).toHaveBeenCalledWith({
      type: "batch",
      answers: {
        "question-1": { type: "option", option_id: "local" },
        "question-2": { type: "text", text: "需要灰度发布" },
      },
    });
  });

  it("submits a cancel answer when the user skips the question", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<UserQuestionPanel question={question} onSubmit={onSubmit} />);
    await user.click(screen.getByRole("button", { name: "跳过，让 AI 自己决定" }));
    expect(onSubmit).toHaveBeenCalledWith({ type: "cancelled" });
  });

  it("submits a cancel answer for the whole batch card", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <UserQuestionPanel
        question={withQuestions([
          {
            id: "question-1",
            question: "选择部署环境",
            options: singleQuestion.options,
            allow_custom_answer: false,
            multi_select: false,
          },
        ])}
        onSubmit={onSubmit}
      />,
    );
    // 单题也是 batch 卡片，跳过按钮文案按题目数量走，不是按协议版本。
    await user.click(screen.getByRole("button", { name: "跳过，让 AI 自己决定" }));
    expect(onSubmit).toHaveBeenCalledWith({ type: "cancelled" });
  });

  it("disables both actions and explains an expired question", () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <UserQuestionPanel
        question={{ ...question, expires_at: "2020-01-01T00:00:00+00:00" }}
        onSubmit={onSubmit}
      />,
    );
    expect(
      (screen.getByRole("button", { name: "提交回答并继续" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "跳过，让 AI 自己决定" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByText("这个问题已过期，发送新消息时会自动跳过。")).toBeTruthy();
  });

  it("switches to expired state when the TTL elapses while mounted", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2029-12-31T23:59:59.000Z"));
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const view = render(
      <UserQuestionPanel
        question={{ ...question, expires_at: "2030-01-01T00:00:00.000Z" }}
        onSubmit={onSubmit}
      />,
    );
    expect(
      (screen.getByRole("button", { name: "跳过，让 AI 自己决定" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    act(() => vi.advanceTimersByTime(1_020));
    expect(
      (screen.getByRole("button", { name: "跳过，让 AI 自己决定" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByText("这个问题已过期，发送新消息时会自动跳过。")).toBeTruthy();
    view.unmount();
    vi.useRealTimers();
  });
});

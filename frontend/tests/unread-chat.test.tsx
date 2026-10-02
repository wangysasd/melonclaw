import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useUnreadChat } from "../src/hooks/useUnreadChat";
import type { ChatMessage } from "../src/hooks/useChatStream";

const message: ChatMessage = { id: "a", role: "assistant", content: "", status: "streaming", markdown: false, events: [], assistantSteps: [], phases: [] };

describe("unread updates while reading history", () => {
  it("does not mark scrolling alone, marks text deltas, and clears on acknowledgement or returning", () => {
    const { result, rerender } = renderHook(({ messages, away }) => useUnreadChat(messages, "user:c1", away, false), { initialProps: { messages: [message], away: false } });
    rerender({ messages: [message], away: true });
    expect(result.current.hasUnread).toBe(false);
    const updated = { ...message, content: "new text" };
    rerender({ messages: [updated], away: true });
    expect(result.current.hasUnread).toBe(true);
    act(() => result.current.acknowledge());
    expect(result.current.hasUnread).toBe(false);
    rerender({ messages: [{ ...updated, content: "new text again" }], away: true });
    expect(result.current.hasUnread).toBe(true);
    rerender({ messages: [updated], away: false });
    expect(result.current.hasUnread).toBe(false);
  });
  it("includes tool results and terminal status but never carries unread across contexts or history reloads", () => {
    const { result, rerender } = renderHook(({ messages, key, loading }) => useUnreadChat(messages, key, true, loading), { initialProps: { messages: [message], key: "user:c1", loading: false } });
    const updated = { ...message, assistantSteps: [{ id: "s", ordinal: 0, status: "completed" as const, content: "", is_final: false, tool_calls: [{ call_id: "c", name: "read_file", batch_index: 0, status: "completed" as const }] }] };
    rerender({ messages: [updated], key: "user:c1", loading: false });
    expect(result.current.hasUnread).toBe(true);
    rerender({ messages: [message], key: "user:c2", loading: false });
    expect(result.current.hasUnread).toBe(false);
    rerender({ messages: [{ ...message, status: "completed" }], key: "user:c2", loading: false });
    expect(result.current.hasUnread).toBe(true);
    rerender({ messages: [], key: "user:c2", loading: true });
    expect(result.current.hasUnread).toBe(false);
    rerender({ messages: [updated], key: "user:c2", loading: false });
    expect(result.current.hasUnread).toBe(false);
  });
});

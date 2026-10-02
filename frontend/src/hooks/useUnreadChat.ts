import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "./useChatStream";

function hasMessageUpdate(previous: ChatMessage[], next: ChatMessage[]) {
  const known = new Map(previous.map((message) => [message.id, message]));
  return next.some((message) => {
    const old = known.get(message.id);
    return !old || old.content !== message.content || old.status !== message.status
      || old.assistantSteps !== message.assistantSteps || old.events !== message.events
      || old.phases.at(-1) !== message.phases.at(-1);
  });
}

/** 消息增量、工具更新或终态才产生未读提示，离开底部本身不等于有新内容。 */
export function useUnreadChat(messages: ChatMessage[], contextKey: string, away: boolean, loading: boolean) {
  const [hasUnread, setHasUnread] = useState(false);
  const previous = useRef({ messages, contextKey, loading });
  useEffect(() => {
    const old = previous.current;
    previous.current = { messages, contextKey, loading };
    if (old.contextKey !== contextKey || old.loading || loading || !away) {
      setHasUnread(false);
    } else if (hasMessageUpdate(old.messages, messages)) {
      setHasUnread(true);
    }
  }, [messages, contextKey, away, loading]);
  return { hasUnread, acknowledge: () => setHasUnread(false) };
}

import type { AssistantStep, DisplayEvent, KnowledgeSourceCard } from "../types/api";
import { knowledgeReferenceKey } from "./knowledgeCitation";

const KNOWLEDGE_TOOLS = new Set(["search_knowledge", "open_knowledge_document"]);

export function messageKnowledgeSources(message: { assistantSteps: AssistantStep[]; events?: DisplayEvent[] }, origin: string) {
  const results = [
    ...message.assistantSteps.flatMap((step) => step.tool_calls.filter((tool) => KNOWLEDGE_TOOLS.has(tool.name))),
    ...(message.events ?? []).filter((event) => KNOWLEDGE_TOOLS.has(String(event.name ?? "")) && event.type === "subagent_tool_result"),
  ];
  const enforce = results.length > 0;
  const cards = results.flatMap((result) => result.knowledge_sources ?? []);
  const keys = enforce ? new Set(cards.flatMap((card) => {
    const key = knowledgeReferenceKey(card.source_url, origin);
    return key ? [key] : [];
  })) : null;
  const listed: KnowledgeSourceCard[] = [];
  const seen = new Set<string>();
  for (const card of cards) {
    if (card.listed === false) continue;
    const key = knowledgeReferenceKey(card.source_url, origin);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    listed.push(card);
  }
  return { listed, keys, enforce };
}

export function knowledgeSourceLabel(card: KnowledgeSourceCard) {
  return ["原文", card.title, card.heading].filter(Boolean).join(" · ");
}

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Select } from "antd";
import Sender, { type SenderRef } from "@ant-design/x/es/sender";

import { Icon } from "./Icon";
import { SkillPicker } from "./SkillPicker";
import { SkillLogo } from "./SkillLogo";
import { findSkillTrigger, type SkillTrigger } from "../lib/skillTrigger";
import { useSession } from "../state/session";
import type { SkillOption } from "../types/api";

const EMPTY_SKILLS: SkillOption[] = [];

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (value: string, skillId?: string | null) => void;
  disabled: boolean;
}

/** Sender 仅负责输入展示；模型快照与发送恢复仍由现有聊天流管理。 */
export function Composer({ value, onChange, onSend, disabled }: ComposerProps) {
  const session = useSession();
  const senderRef = useRef<SenderRef>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const skillPrefixRef = useRef<HTMLDivElement>(null);
  const composingRef = useRef(false);
  const submittingRef = useRef(false);
  const [skillTrigger, setSkillTrigger] = useState<SkillTrigger | null>(null);
  const [activeSkillIndex, setActiveSkillIndex] = useState(0);
  const [selectedSkill, setSelectedSkill] = useState<SkillOption | null>(null);
  const [skillPrefixWidth, setSkillPrefixWidth] = useState(0);

  const skills = session.skills ?? EMPTY_SKILLS;
  const filteredSkills = useMemo(() => {
    const query = skillTrigger?.query.trim().toLocaleLowerCase() ?? "";
    if (!query) return skills;
    return skills.filter((skill) =>
      `${skill.id} ${skill.display_name} ${skill.description}`
        .toLocaleLowerCase()
        .includes(query),
    );
  }, [skillTrigger?.query, skills]);

  const updateSkillTrigger = (
    nextValue: string,
    input?: HTMLTextAreaElement | null,
  ) => {
    const target = input ?? (senderRef.current?.inputElement as HTMLTextAreaElement | undefined);
    const cursor = target?.selectionStart ?? nextValue.length;
    const selectionEnd = target?.selectionEnd ?? cursor;
    const nextTrigger = findSkillTrigger(nextValue, cursor, selectionEnd);
    setSkillTrigger(nextTrigger);
    if (!nextTrigger) {
      setActiveSkillIndex(0);
      return;
    }
    setActiveSkillIndex((index) => {
      const nextLength = skills.filter((skill) => {
        const query = nextTrigger.query.trim().toLocaleLowerCase();
        return !query || `${skill.id} ${skill.display_name} ${skill.description}`
          .toLocaleLowerCase()
          .includes(query);
      }).length;
      return nextLength > 0 ? Math.min(index, nextLength - 1) : 0;
    });
  };

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (!composerRef.current?.contains(event.target as Node)) {
        setSkillTrigger(null);
      }
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, []);

  useEffect(() => {
    // 技能选择只对当前上下文的一条消息生效，切换用户/项目/会话时不能带到下一处。
    setSelectedSkill(null);
    setSkillTrigger(null);
  }, [session.userId, session.projectId, session.conversationId]);

  useLayoutEffect(() => {
    if (!selectedSkill) {
      setSkillPrefixWidth(0);
      return;
    }
    setSkillPrefixWidth(skillPrefixRef.current?.offsetWidth ?? 0);
  }, [selectedSkill]);

  useEffect(() => {
    setActiveSkillIndex((index) =>
      filteredSkills.length > 0 ? Math.min(index, filteredSkills.length - 1) : 0,
    );
  }, [filteredSkills.length]);

  let placeholder = "聊点儿什么，输入/调用技能工具。";
  if (!session.contextReady) {
    placeholder = "正在准备工作区…";
  } else if (session.projects.length === 0) {
    placeholder = "请先创建一个项目…";
  } else if (session.conversationCreating) {
    placeholder = "正在准备会话…";
  } else if (session.runStatus === "waiting") {
    placeholder = "请先处理待确认操作，也可以先写下一条消息…";
  } else if (session.busy) {
    placeholder = "助手正在回复，可以先写下一条消息…";
  }

  const canUse =
    session.contextReady &&
    session.status?.status === "ready" &&
    session.projects.length > 0;

  const inputDisabled = !canUse;
  const sendDisabled = inputDisabled || disabled || session.busy || session.runStatus === "waiting" || session.conversationCreating || !value.trim();
  const modelOptions = session.modelOptions ?? [];
  const selectedModelId =
    session.selectedModelId || modelOptions.find((item) => item.available)?.id || "";
  const sendLabel = session.conversationCreating
    ? "准备中"
    : session.runStatus === "waiting"
      ? "等待确认"
      : session.busy
        ? "处理中"
        : "发送";
  const sendIcon = session.conversationCreating || session.busy
    ? "loader-circle"
    : session.runStatus === "waiting"
      ? "shield-check"
      : "arrow-up";

  const submit = () => {
    if (sendDisabled || composingRef.current || submittingRef.current) return;
    submittingRef.current = true;
    try {
      if (selectedSkill) {
        onSend(value, selectedSkill.id);
      } else {
        onSend(value);
      }
    } finally {
      setSelectedSkill(null);
      queueMicrotask(() => { submittingRef.current = false; });
    }
  };

  const selectSkill = (skill: SkillOption) => {
    if (!skillTrigger) return;
    const nextValue = value.slice(0, skillTrigger.start) + value.slice(skillTrigger.end);
    const nextCursor = skillTrigger.start;
    onChange(nextValue);
    setSelectedSkill(skill);
    setSkillTrigger(null);
    setActiveSkillIndex(0);
    window.requestAnimationFrame(() => {
      const input = senderRef.current?.inputElement as HTMLTextAreaElement | undefined;
      input?.focus();
      input?.setSelectionRange(nextCursor, nextCursor);
    });
  };

  return (
    <div className="composer-wrap">
      <div ref={composerRef} className="composer" onCompositionStartCapture={() => { composingRef.current = true; }}
        onCompositionEndCapture={() => { composingRef.current = false; }}>
        {skillTrigger ? (
          <SkillPicker
            skills={filteredSkills}
            activeIndex={activeSkillIndex}
            loading={session.skillsLoading ?? false}
            error={session.skillsError ?? null}
            onSelect={selectSkill}
            onHover={setActiveSkillIndex}
          />
        ) : null}
        <Sender
          ref={senderRef}
          className="melon-sender"
          value={value}
          placeholder={selectedSkill ? "" : placeholder}
          styles={{
            input: {
              textIndent: selectedSkill ? `${skillPrefixWidth + 6}px` : undefined,
            },
          }}
          aria-label="输入内容"
          aria-describedby="composer-hint"
          disabled={inputDisabled}
          loading={session.busy || session.conversationCreating}
          onChange={(nextValue, event) => {
            onChange(nextValue);
            updateSkillTrigger(
              nextValue,
              event?.currentTarget as HTMLTextAreaElement | undefined,
            );
          }}
          onFocus={(event) => updateSkillTrigger(value, event.currentTarget)}
          onSubmit={submit}
          submitType="enter"
          autoSize={{ minRows: 1, maxRows: 6 }}
          prefix={
            selectedSkill ? (
              <div
                ref={skillPrefixRef}
                className="selected-skill"
                aria-label={`已选择技能 ${selectedSkill.display_name}`}
                aria-keyshortcuts="Backspace"
                title="按 Backspace 移除技能"
              >
                <SkillLogo />
                <span>{selectedSkill.display_name}</span>
              </div>
            ) : null
          }
          suffix={false}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || event.keyCode === 229 || composingRef.current) return false;
            const input = event.currentTarget as HTMLTextAreaElement;
            if (
              selectedSkill &&
              !skillTrigger &&
              event.key === "Backspace" &&
              input.selectionStart === 0 &&
              input.selectionEnd === 0
            ) {
              event.preventDefault();
              setSelectedSkill(null);
              return false;
            }
            if (skillTrigger) {
              if (event.key === "Escape") {
                event.preventDefault();
                setSkillTrigger(null);
                return false;
              }
              if (event.key === "ArrowDown" && filteredSkills.length > 0) {
                event.preventDefault();
                setActiveSkillIndex((index) => (index + 1) % filteredSkills.length);
                return false;
              }
              if (event.key === "ArrowUp" && filteredSkills.length > 0) {
                event.preventDefault();
                setActiveSkillIndex((index) => (index - 1 + filteredSkills.length) % filteredSkills.length);
                return false;
              }
              if (
                (event.key === "Enter" || event.key === "Tab") &&
                !event.shiftKey &&
                !event.ctrlKey &&
                !event.altKey &&
                !event.metaKey &&
                filteredSkills.length > 0
              ) {
                event.preventDefault();
                selectSkill(filteredSkills[activeSkillIndex]);
                return false;
              }
            }
            if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey) {
              event.preventDefault();
              submit();
              return false;
            }
          }}
          footer={
            <div className="composer-bottom">
              {modelOptions.length > 0 ? (
                <Select
                  className="model-picker"
                  aria-label="选择模型"
                  value={selectedModelId || undefined}
                  disabled={inputDisabled || session.conversationCreating}
                  title="模型选择从下一条消息生效"
                  options={modelOptions.map((option) => ({
                    key: option.id,
                    value: option.id,
                    label: option.model,
                    disabled: !option.available,
                  }))}
                  onChange={(modelId) => session.selectModel?.(modelId)}
                />
              ) : null}
              <button
                className="send-button"
                type="button"
                onClick={submit}
                disabled={sendDisabled}
                aria-label={sendLabel}
                aria-busy={session.busy || session.conversationCreating}
                title={sendLabel}
              >
                <Icon
                  name={sendIcon}
                  size={18}
                  className={sendIcon === "loader-circle" ? "send-arrow mc-icon-spin" : "send-arrow"}
                />
              </button>
            </div>
          }
        />
      </div>
      <div className="composer-meta">
        <div className="composer-hint" id="composer-hint">
          <Icon name="message-circle" size={15} /> Enter 发送 · Shift + Enter 换行
        </div>
        <div className="composer-footnote">AI生成内容仅供参考。</div>
      </div>
    </div>
  );
}

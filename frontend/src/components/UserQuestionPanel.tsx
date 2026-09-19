import { useId, useMemo, useRef, useState } from "react";

import { Icon } from "./Icon";
import type {
  SingleUserInputAnswer,
  UserInputAnswer,
  UserQuestionItem,
  UserQuestionRequest,
} from "../types/api";
import { useUserQuestionExpired } from "../lib/userQuestionExpiry";

interface UserQuestionPanelProps {
  question: UserQuestionRequest;
  onSubmit: (answer: UserInputAnswer) => Promise<void>;
  expired?: boolean;
}

const CUSTOM_OPTION_ID = "__custom_answer__";

interface QuestionState {
  selected: string[];
  customText: string;
}

function createQuestionStates(questions: UserQuestionItem[]): QuestionState[] {
  return questions.map(() => ({ selected: [], customText: "" }));
}

/**
 * 题目指纹：题号、题干与选项集合的摘要。
 *
 * 同一个 interaction_id 也可能换了另一道题（助手连续提问时账本会复用），
 * 指纹变化时必须丢弃上一道题的选择和输入，否则会把旧答案发给新问题。
 */
function questionFingerprint(questions: UserQuestionItem[]): string {
  return questions
    .map(
      (item) =>
        `${item.id}|${item.question}|${(item.options ?? [])
          .map((option) => option.id)
          .join(",")}`,
    )
    .join("||");
}

function canSubmitQuestion(
  question: UserQuestionItem,
  state: QuestionState,
): boolean {
  const customSelected = state.selected.includes(CUSTOM_OPTION_ID);
  const selectedOptionIds = state.selected.filter(
    (value) => value !== CUSTOM_OPTION_ID,
  );
  if (question.multi_select) {
    return selectedOptionIds.length > 0 || state.customText.trim() !== "";
  }
  if (question.options.length === 0) {
    return question.allow_custom_answer && state.customText.trim() !== "";
  }
  return (
    state.selected.length > 0 &&
    (!customSelected || state.customText.trim() !== "")
  );
}

function buildAnswer(
  question: UserQuestionItem,
  state: QuestionState,
): SingleUserInputAnswer {
  const customSelected = state.selected.includes(CUSTOM_OPTION_ID);
  const selectedOptionIds = state.selected.filter(
    (value) => value !== CUSTOM_OPTION_ID,
  );
  if (question.multi_select) {
    return {
      type: "options",
      option_ids: selectedOptionIds,
      ...(state.customText.trim() ? { text: state.customText.trim() } : {}),
    };
  }
  if (customSelected || question.options.length === 0) {
    return { type: "text", text: state.customText.trim() };
  }
  return { type: "option", option_id: state.selected[0] };
}

export function UserQuestionPanel({
  question,
  onSubmit,
  expired: expiredOverride,
}: UserQuestionPanelProps) {
  const id = useId();
  const submittingRef = useRef(false);
  const questions = question.questions;
  const fingerprint = useMemo(() => questionFingerprint(questions), [questions]);
  const [questionStates, setQuestionStates] = useState<QuestionState[]>(() =>
    createQuestionStates(questions),
  );
  const [stateFingerprint, setStateFingerprint] = useState(fingerprint);
  if (stateFingerprint !== fingerprint) {
    // 换题了：立即丢弃上一道题的选择与输入，避免答案串到新问题。
    setStateFingerprint(fingerprint);
    setQuestionStates(createQuestionStates(questions));
  }
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  // 文案按题目数量走，不再按协议版本：单题就是长度为一的那张卡片。
  const multipleQuestions = questions.length > 1;
  const liveExpired = useUserQuestionExpired(question.expires_at);
  const expired = expiredOverride ?? liveExpired;
  const allAnswered =
    questions.length > 0 &&
    questions.every((item, index) => canSubmitQuestion(item, questionStates[index]));

  const updateQuestion = (index: number, state: QuestionState) => {
    setQuestionStates((previous) =>
      previous.map((current, currentIndex) =>
        currentIndex === index ? state : current,
      ),
    );
    setSubmitError("");
  };

  const handleSkip = async () => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setSubmitError("");
    try {
      await onSubmit({ type: "cancelled" });
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : "跳过未成功，请重试。",
      );
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const handleSubmit = async () => {
    if (!allAnswered || submittingRef.current) return;
    const answers = questions.map((item, index) =>
      buildAnswer(item, questionStates[index]),
    );
    const answer: UserInputAnswer = {
      type: "batch",
      answers: Object.fromEntries(
        questions.map((item, index) => [item.id, answers[index]]),
      ),
    };
    submittingRef.current = true;
    setSubmitting(true);
    setSubmitError("");
    try {
      await onSubmit(answer);
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : "提交未成功，请重试。",
      );
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <section
      className="approval-panel user-question-panel"
      aria-labelledby={`${id}-title`}
      aria-busy={submitting}
    >
      <div
        className="approval-title user-question-title"
        id={`${id}-title`}
        role="status"
      >
        <Icon name="message-circle" size={19} />
        {questions.length > 1 ? "请回答以下问题" : "等待你的回答"}
      </div>
      {questions.map((item, index) => {
        const state = questionStates[index] ?? { selected: [], customText: "" };
        const customSelected = state.selected.includes(CUSTOM_OPTION_ID);
        const questionLabel = questions.length > 1
          ? `第 ${index + 1} 个问题的自定义回答`
          : "自定义回答";
        return (
          <div className="user-question-item" key={item.id}>
            <div className="user-question-copy">
              {questions.length > 1 ? `${index + 1}. ` : ""}
              {item.question}
            </div>
            {item.options.length > 0 ? (
              <fieldset className="user-question-options">
                <legend>{item.multi_select ? "可多选" : "请选择一个选项"}</legend>
                {item.options.map((option) => (
                  <label className="user-question-option" key={option.id}>
                    <input
                      type={item.multi_select ? "checkbox" : "radio"}
                      name={`${id}-${item.id}-options`}
                      value={option.id}
                      aria-label={option.label}
                      checked={state.selected.includes(option.id)}
                      disabled={submitting}
                      onChange={() => {
                        const selected = !item.multi_select
                          ? [option.id]
                          : state.selected.includes(option.id)
                            ? state.selected.filter((value) => value !== option.id)
                            : [...state.selected, option.id];
                        updateQuestion(index, { ...state, selected });
                      }}
                    />
                    <span>
                      <span className="user-question-option-label">
                        {option.label}
                      </span>
                      {option.description ? (
                        <span className="user-question-option-description">
                          {option.description}
                        </span>
                      ) : null}
                    </span>
                  </label>
                ))}
                {item.allow_custom_answer && !item.multi_select ? (
                  <label className="user-question-option">
                    <input
                      type="radio"
                      name={`${id}-${item.id}-options`}
                      value={CUSTOM_OPTION_ID}
                      aria-label="其他"
                      checked={customSelected}
                      disabled={submitting}
                      onChange={() =>
                        updateQuestion(index, {
                          ...state,
                          selected: [CUSTOM_OPTION_ID],
                        })
                      }
                    />
                    <span>其他</span>
                  </label>
                ) : null}
              </fieldset>
            ) : null}
            {item.allow_custom_answer ? (
              <textarea
                className="user-question-text"
                value={state.customText}
                disabled={
                  submitting ||
                  (!item.multi_select && item.options.length > 0 && !customSelected)
                }
                placeholder={
                  item.multi_select
                    ? "可选填写其他回答"
                    : item.options.length > 0
                      ? "选择“其他”后填写你的回答"
                      : "填写你的回答"
                }
                aria-label={questionLabel}
                onChange={(event) =>
                  updateQuestion(index, {
                    ...state,
                    customText: event.target.value,
                  })
                }
              />
            ) : null}
          </div>
        );
      })}
      {submitError ? (
        <div className="approval-field-error" role="alert">
          {submitError}
        </div>
      ) : null}
      <div className="user-question-actions">
        <button
          type="button"
          className="approval-submit"
          disabled={submitting || expired || !allAnswered}
          onClick={() => void handleSubmit()}
        >
          {submitting
            ? "正在提交回答…"
            : multipleQuestions
              ? "提交全部回答并继续"
              : "提交回答并继续"}
        </button>
        <button
          type="button"
          className="user-question-skip"
          disabled={submitting || expired}
          onClick={() => void handleSkip()}
        >
          {submitting
            ? "正在跳过…"
            : multipleQuestions
              ? "全部跳过，让 AI 自己决定"
              : "跳过，让 AI 自己决定"}
        </button>
      </div>
      {expired ? (
        <p className="user-question-expired" role="status">
          这个问题已过期，发送新消息时会自动跳过。
        </p>
      ) : null}
    </section>
  );
}

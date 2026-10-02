import { useState } from "react";

import { Icon } from "./Icon";
import type { SkillOption } from "../types/api";

export interface PlusMenuProps {
  skills: SkillOption[];
  skillsLoading: boolean;
  skillsError: string | null;
  /** 选择「图片和文件」：打开添加附件弹窗。 */
  onPickFiles: () => void;
  /** 选中某个技能，等价于在输入框里用 `/` 选择。 */
  onPickSkill: (skill: SkillOption) => void;
}

/**
 * 输入框左下角加号的二级目录。
 * 一级：图片和文件 / 技能；鼠标悬停「技能」时在右侧展开技能列表，
 * 列表项直接复用聊天框内 `/` 技能面板的样式。
 */
export function PlusMenu({
  skills,
  skillsLoading,
  skillsError,
  onPickFiles,
  onPickSkill,
}: PlusMenuProps) {
  const [pane, setPane] = useState<"root" | "skills">("root");

  return (
    <div className="plus-menu" role="menu" aria-label="添加内容">
      <button
        type="button"
        role="menuitem"
        className="plus-menu-item"
        onPointerEnter={() => setPane("root")}
        onFocus={() => setPane("root")}
        onClick={onPickFiles}
      >
        <Icon name="file-text" size={16} />
        <span className="plus-menu-label">图片和文件</span>
      </button>

      {/*
        二级目录只在「一级目录切到其他项」时才收起：这里不在「技能」这一行上监听
        pointerleave，否则鼠标从技能行移向飞出的二级目录（哪怕只是擦过按钮与
        目录之间的间隙）就会把目录收掉，用户很难选到技能。
      */}
      <div className="plus-menu-sub">
        <button
          type="button"
          role="menuitem"
          className={`plus-menu-item${pane === "skills" ? " is-open" : ""}`}
          aria-haspopup="menu"
          aria-expanded={pane === "skills"}
          onPointerEnter={() => setPane("skills")}
          onFocus={() => setPane("skills")}
          onClick={() => setPane((current) => (current === "skills" ? "root" : "skills"))}
          onKeyDown={(event) => {
            if (event.key === "ArrowRight") {
              event.preventDefault();
              setPane("skills");
            }
            if (event.key === "ArrowLeft") {
              event.preventDefault();
              setPane("root");
            }
          }}
        >
          <Icon name="list-checks" size={16} />
          <span className="plus-menu-label">技能</span>
          <Icon name="chevron-right" size={16} className="plus-menu-arrow" />
        </button>

        {pane === "skills" ? (
          <div className="plus-menu-flyout">
            <div className="plus-menu-panel">
              <div className="plus-menu-skills" role="listbox" aria-label="技能列表">
                {skillsLoading ? (
                  <div className="skill-picker-state" role="status">正在加载技能…</div>
                ) : skillsError ? (
                  <div className="skill-picker-state" role="status">技能目录暂不可用</div>
                ) : skills.length === 0 ? (
                  <div className="skill-picker-state" role="status">没有可用技能</div>
                ) : (
                  skills.map((skill) => (
                    <button
                      key={skill.id}
                      type="button"
                      role="option"
                      aria-selected={false}
                      className="skill-picker-item"
                      title={skill.description}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => onPickSkill(skill)}
                    >
                      <span className="skill-picker-copy">
                        <span className="skill-picker-title">{skill.display_name}</span>
                        <span className="skill-picker-description">{skill.description}</span>
                      </span>
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

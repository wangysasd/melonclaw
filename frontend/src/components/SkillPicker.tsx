import type { SkillOption } from "../types/api";

export interface SkillPickerProps {
  skills: SkillOption[];
  activeIndex: number;
  loading: boolean;
  error: string | null;
  onSelect: (skill: SkillOption) => void;
  onHover: (index: number) => void;
}

export function SkillPicker({
  skills,
  activeIndex,
  loading,
  error,
  onSelect,
  onHover,
}: SkillPickerProps) {
  return (
    <div className="skill-picker" role="dialog" aria-label="可选技能">
      <div className="skill-picker-header">
        <span>可选技能</span>
      </div>
      <div className="skill-picker-list" role="listbox" aria-label="技能列表">
        {loading ? (
          <div className="skill-picker-state" role="status">正在加载技能…</div>
        ) : error ? (
          <div className="skill-picker-state" role="status">技能目录暂不可用</div>
        ) : skills.length === 0 ? (
          <div className="skill-picker-state" role="status">没有匹配的技能</div>
        ) : (
          skills.map((skill, index) => (
            <button
              key={skill.id}
              id={`skill-option-${skill.id}`}
              className={`skill-picker-item${index === activeIndex ? " is-active" : ""}`}
              type="button"
              role="option"
              aria-selected={index === activeIndex}
              title={skill.description}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => onHover(index)}
              onClick={() => onSelect(skill)}
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
  );
}

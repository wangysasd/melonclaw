import { useEffect, useRef, useState } from "react";

import { Icon } from "./Icon";
import { useSession } from "../state/session";

interface ProjectPickerProps {
  onOpenProjectDialog?: () => void;
}

/** 输入框上方的项目选择器；与侧栏共用 session.projectId。 */
export function ProjectPicker({ onOpenProjectDialog }: ProjectPickerProps) {
  const session = useSession();
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const selected = session.projects.find((project) => project.id === session.projectId);
  const filtered = session.projects.filter((project) =>
    project.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  );

  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const close = () => {
    setOpen(false);
    setQuery("");
  };

  const selectProject = (projectId: string) => {
    close();
    if (projectId !== session.projectId) void session.openProject(projectId);
  };

  const leaveProject = () => {
    close();
    if (session.projectId) session.closeProject();
  };

  return (
    <div className="composer-project-bar">
      <div className="composer-project-picker" ref={rootRef}>
        <div className={`composer-project-control${selected ? " is-selected" : ""}`}>
          {selected ? (
            <button
              type="button"
              className="composer-project-clear"
              aria-label={`退出项目「${selected.name}」`}
              title="不在项目中工作"
              disabled={!session.contextReady}
              onClick={leaveProject}
            >
              <Icon name="folder-open" size={14} className="composer-project-folder" />
              <Icon name="x" size={14} className="composer-project-remove" />
            </button>
          ) : null}
          <button
            type="button"
            className="composer-project-trigger"
            aria-label={selected ? `当前项目：${selected.name}，选择项目` : "选择项目"}
            aria-haspopup="dialog"
            aria-expanded={open}
            disabled={!session.contextReady}
            onClick={() => setOpen((current) => !current)}
          >
            {!selected ? <Icon name="folder" size={14} /> : null}
            <span className="composer-project-name">{selected?.name ?? "选择项目"}</span>
            <Icon name="chevron-down" size={12} />
          </button>
        </div>

        {open ? (
          <div className="composer-project-popover" role="dialog" aria-label="选择项目">
            <label className="composer-project-search">
              <Icon name="search" size={16} />
              <input
                ref={searchRef}
                type="search"
                value={query}
                placeholder="搜索项目"
                aria-label="搜索项目"
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <div className="composer-project-options" role="listbox" aria-label="项目列表">
              {filtered.length > 0 ? filtered.map((project) => (
                <button
                  key={project.id}
                  type="button"
                  className="composer-project-option"
                  role="option"
                  aria-selected={project.id === session.projectId}
                  onClick={() => selectProject(project.id)}
                >
                  <Icon name="folder" size={16} />
                  <span>{project.name}</span>
                  {project.id === session.projectId ? <Icon name="circle-check" size={16} className="composer-project-check" /> : null}
                </button>
              )) : (
                <div className="composer-project-empty">没有匹配的项目</div>
              )}
            </div>
            <div className="composer-project-actions">
              <button type="button" onClick={() => { close(); onOpenProjectDialog?.(); }}>
                <Icon name="plus" size={16} /> 新建项目
              </button>
              <button type="button" onClick={leaveProject}>
                <Icon name="x" size={16} /> 不在项目中工作
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

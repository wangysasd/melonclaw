import { useRef, useState } from "react";
import { Popover, Tooltip } from "antd";
import { Icon } from "./Icon";
import { roleNames, type KnowledgeBase } from "../api/knowledge";

interface KnowledgePickerProps {
  bases: KnowledgeBase[];
  selectedIds: string[];
  loading: boolean;
  error: string;
  onSearch: (query: string) => void;
  onChange: (ids: string[]) => void;
}

/** 与项目入口共用视觉样式，选择多份资料时保持弹层打开。 */
export function KnowledgePicker({ bases, selectedIds, loading, error, onSearch, onChange }: KnowledgePickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const filtered = bases.filter((base) => base.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const names = selectedIds.map((id) => bases.find((base) => base.id === id)?.name).filter(Boolean).join("、");
  const changeOpen = (next: boolean) => {
    setOpen(next);
    if (!next) { setQuery(""); onSearch(""); }
  };
  return <Popover
    trigger="click"
    placement="topLeft"
    open={open}
    onOpenChange={changeOpen}
    afterOpenChange={(visible) => { if (visible) searchRef.current?.focus(); }}
    classNames={{ root: "composer-knowledge-popover" }}
    styles={{ container: { padding: 0, background: "transparent", boxShadow: "none", border: 0 } }}
    content={<div className="composer-project-popover composer-knowledge-panel" role="dialog" aria-label="选择知识库" onKeyDown={(event) => {
      if (event.key === "Escape") { event.stopPropagation(); changeOpen(false); }
    }}>
      <label className="composer-project-search">
        <Icon name="search" size={16} />
        <input ref={searchRef} type="search" aria-label="搜索知识库" placeholder="搜索知识库" value={query} maxLength={255} onChange={(event) => { setQuery(event.target.value); onSearch(event.target.value); }} />
      </label>
      <div className="composer-project-options" aria-label="知识库列表" aria-busy={loading}>
        {filtered.map((base) => {
          const checked = selectedIds.includes(base.id);
          return <button key={base.id} type="button" className="composer-project-option" role="checkbox" aria-checked={checked} disabled={!checked && selectedIds.length >= 20} onClick={() => onChange(checked ? selectedIds.filter((id) => id !== base.id) : [...selectedIds, base.id])}>
            <Icon name="knowledge-books" size={16} />
            <span title={base.name}>{base.name}<small className="composer-knowledge-role">知识库 · {roleNames[base.role]}</small></span>
            {checked ? <Icon name="circle-check" size={16} className="composer-project-check" /> : null}
          </button>;
        })}
        {error ? <div className="composer-project-empty" role="alert">{error}<button type="button" className="composer-project-trigger" onClick={() => onSearch(query)}>重试</button></div> : loading ? <div className="composer-project-empty" role="status">正在加载…</div> : !filtered.length ? <div className="composer-project-empty">没有匹配的知识库</div> : null}
      </div>
      <div className="composer-project-actions composer-knowledge-actions">
        <span>已选 {selectedIds.length}/20</span>
        <button type="button" disabled={!selectedIds.length} onClick={() => onChange([])}><Icon name="x" size={14} />清空选择</button>
      </div>
    </div>}
  >
    <Tooltip title={names || "选择知识库（可多选）"} placement="top" open={open ? false : undefined}>
      <button type="button" className="composer-project-trigger composer-knowledge-trigger" aria-label="本轮使用知识库" aria-haspopup="dialog" aria-expanded={open}>
        <Icon name={selectedIds.length ? "knowledge-books-filled" : "knowledge-books"} size={14} />
        <span className="composer-project-name">{selectedIds.length ? `知识库 · 已选 ${selectedIds.length}` : "选择知识库"}</span>
        <Icon name="chevron-down" size={12} />
      </button>
    </Tooltip>
  </Popover>;
}

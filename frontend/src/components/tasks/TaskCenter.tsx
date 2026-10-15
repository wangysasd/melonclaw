import { Alert, App, Button, Input, Select, Space, Table } from "antd";
import { useCallback, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { taskApi, type Task } from "../../api/tasks";
import { createRequestId } from "../../lib/requestId";
import { Icon } from "../Icon";
import { tokens } from "../../theme/tokens";
import { useSession } from "../../state/session";
import { StatusTag, statusNames, TaskTime } from "./presentation";
import { scheduleLabel, zonedLocal } from "./schedule";
import { TaskEditor } from "./TaskEditor";
import { TaskRunsDrawer } from "./TaskRunsDrawer";
import { useTaskPolling } from "./useTaskPolling";
import "../../styles/tasks.css";

export default function TaskCenter({ onOpenConversation }: { onOpenConversation?: (conversationId: string) => void }) {
  const session = useSession();
  const { message, modal } = App.useApp();
  const [name, setName] = useState("");
  const [status, setStatus] = useState<string>();
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [historyTaskId, setHistoryTaskId] = useState<string>();
  const [editor, setEditor] = useState<Task | "new">();
  const [busy, setBusy] = useState<Record<string, "runs" | "archive" | "enabled">>({});
  const pendingTasks = useRef(new Set<string>());
  const keys = useRef(new Map<string, string>());
  const load = useCallback(async (signal: AbortSignal) => {
    const [list, health] = await Promise.all([taskApi.list({ name, status, page }, signal), taskApi.status(signal)]);
    return { list, health, revision };
  }, [name, status, page, revision]);
  const { data, error } = useTaskPolling(load, 10000);
  const act = async (task: Task, action: "runs" | "archive" | "enabled") => {
    if (pendingTasks.current.has(task.id)) return;
    pendingTasks.current.add(task.id);
    const identity = `${task.id}:${task.version}:${action}`;
    if (!keys.current.has(identity)) keys.current.set(identity, createRequestId());
    setBusy(current => ({ ...current, [task.id]: action }));
    try {
      if (action === "enabled") await taskApi.enable(task, task.status !== "enabled", keys.current.get(identity)!);
      else await taskApi.action(task, action, keys.current.get(identity)!);
      keys.current.delete(identity);
      setRevision(n => n + 1);
      if (action === "runs") { setHistoryTaskId(undefined); onOpenConversation?.(task.conversation_id); }
      void message.success(action === "runs" ? "已加入运行队列" : action === "archive" ? "任务已归档" : "任务启用状态已更新");
    } catch (err) {
      void message.error(err instanceof Error ? err.message : "操作失败");
      if (err instanceof ApiError && err.status > 0 && err.status < 500) keys.current.delete(identity);
    } finally {
      pendingTasks.current.delete(task.id);
      setBusy(current => { const next = { ...current }; delete next[task.id]; return next; });
    }
  };
  const enable = (task: Task) => modal.confirm({ title: task.status === "enabled" ? "暂停后续计划？" : "恢复后续计划？", content: "已排队或执行中的运行会继续，仍可立即运行；恢复循环计划只安排未来周期，过期的一次性计划不补跑。", okText: "确认", cancelText: "返回", onOk: () => act(task, "enabled") });
  const archive = (task: Task) => modal.confirm({
    rootClassName: "task-archive-confirm", centered: true, width: 440, icon: null,
    styles: { container: { padding: tokens["space-6"] }, body: { padding: 0 } },
    title: "归档任务？",
    content: <div className="task-archive-content">
      <div className="task-archive-target"><span>任务名称</span><strong>{task.name}</strong></div>
      <ul className="task-archive-impact">
        <li>保留历史结果和任务会话。</li>
        <li>取消排队中的运行，已开始的运行会继续完成。</li>
        <li>停止后续计划，归档后无法编辑或再次运行。</li>
      </ul>
    </div>,
    okText: "确认归档", cancelText: "取消", focusable: { autoFocusButton: "cancel" },
    onOk: () => act(task, "archive"),
  });
  return <main className="task-center">
    <header className="task-header"><div><h1>定时任务</h1><p>配置执行计划，结果自动进入每个任务的固定会话。</p></div><Button type="primary" icon={<Icon name="plus" size={16} />} onClick={() => setEditor("new")}>新建任务</Button></header>
    {error && <Alert type="error" showIcon title={error} />}
    {data && !data.health.executor.ready && <Alert type="warning" showIcon title="任务调度尚未就绪" description={data.health.executor.error || "任务调度随 Web 自动启动，请稍候或检查服务状态。"} />}
      <Space wrap className="task-filters">
        <Input.Search aria-label="搜索任务" placeholder="搜索任务名称" allowClear onSearch={v => { setName(v); setPage(1); }} style={{ width: 240 }} />
        <Select aria-label="任务状态" placeholder="全部状态" allowClear value={status} onChange={v => { setStatus(v); setPage(1); }} style={{ width: 140 }} options={[{ value: "enabled", label: "已启用" }, { value: "paused", label: "已暂停" }, { value: "archived", label: "已归档" }]} />
        <Button icon={<Icon name="refresh-cw" size={14} />} onClick={() => setRevision(n => n + 1)}>刷新</Button>
      </Space>
      <Table<Task> className="task-list" tableLayout="fixed" rowKey="id" size="middle" dataSource={data?.list.items ?? []} loading={!data && !error} scroll={{ x: 1240 }} locale={{ emptyText: "还没有任务，点击新建任务开始" }}
        pagination={{ current: page, total: data?.list.total ?? 0, pageSize: 20, showSizeChanger: false, onChange: setPage }}
        columns={[
          { title: "任务名称", dataIndex: "name", width: 220, render: (v: string, task) => <Button type="link" className="task-name-link" onClick={() => onOpenConversation?.(task.conversation_id)}>{v}</Button> },
          { title: "任务模型", width: 160, render: (_, task) => session.modelOptions.find(m => m.id === task.definition.model_id)?.display_name ?? task.definition.model_id },
          { title: "执行计划", width: 160, render: (_, task) => <div>{task.definition.schedule.kind === "once" ? <><span>一次</span><div>{task.definition.schedule.run_at ? zonedLocal(task.definition.schedule.run_at).replace("T", " ") : "—"}</div></> : scheduleLabel(task.definition.schedule)}</div> },
          { title: "状态", dataIndex: "status", width: 90, render: v => <StatusTag status={v} /> },
          { title: "最近一次运行", width: 190, render: (_, task) => task.latest_run ? <Button type="link" className="task-run-link" aria-label={`查看${task.name}的运行记录，最近一次${statusNames[task.latest_run.status]}，${zonedLocal(task.latest_run.started_at ?? task.latest_run.created_at).replace("T", " ")}`} onClick={() => setHistoryTaskId(task.id)}><StatusTag status={task.latest_run.status} /><TaskTime value={task.latest_run.started_at ?? task.latest_run.created_at} /></Button> : "尚未运行" },
          { title: "下一次运行", width: 130, render: (_, task) => task.status === "paused" ? "已暂停" : task.status === "archived" ? "已归档" : task.next_run_at ? <TaskTime value={task.next_run_at} /> : task.definition.schedule.kind === "once" ? "计划已结束" : task.definition.schedule.kind === "manual" ? "手动运行" : "—" },
          { title: "操作", width: 290, render: (_, task) => <Space size={4} className="task-actions"><Button type="text" size="small" onClick={() => onOpenConversation?.(task.conversation_id)}>查看会话</Button><Button className="task-action-run" size="small" disabled={task.status === "archived" || Boolean(busy[task.id]) || task.has_active_run} loading={busy[task.id] === "runs"} onClick={() => void act(task, "runs")}>运行</Button><Button type="text" size="small" disabled={task.status === "archived" || Boolean(busy[task.id])} onClick={() => setEditor(task)}>编辑</Button><Button type="text" danger size="small" loading={busy[task.id] === "archive"} disabled={task.status === "archived" || Boolean(busy[task.id])} onClick={() => archive(task)}>归档</Button><Button type="text" size="small" loading={busy[task.id] === "enabled"} disabled={task.status === "archived" || Boolean(busy[task.id])} onClick={() => enable(task)}>{task.status === "enabled" ? "暂停" : "恢复"}</Button></Space> },
        ]} />
    {historyTaskId && <TaskRunsDrawer key={historyTaskId} id={historyTaskId} revision={revision} onClose={() => setHistoryTaskId(undefined)} onChanged={() => setRevision(n => n + 1)} />}
    {editor && <TaskEditor key={editor === "new" ? "new" : `${editor.id}:${editor.version}`} task={editor === "new" ? undefined : editor} onClose={() => setEditor(undefined)} onSaved={(_, conversationId) => { if (conversationId) onOpenConversation?.(conversationId); setEditor(undefined); setRevision(n => n + 1); }} />}
  </main>;
}

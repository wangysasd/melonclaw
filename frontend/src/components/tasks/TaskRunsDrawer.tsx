import { tablePagination } from "../tablePresentation";
import { Alert, App, Button, Collapse, Descriptions, Drawer, Empty, Space, Table } from "antd";
import { useCallback, useRef, useState } from "react";
import { taskApi, type TaskRun } from "../../api/tasks";
import { ApiError } from "../../api/client";
import { createRequestId } from "../../lib/requestId";
import { StatusTag, time } from "./presentation";
import { useTaskPolling } from "./useTaskPolling";

function RunRecord({ id, taskId, onChanged }: { id: string; taskId: string; onChanged: () => void }) {
  const { message, modal } = App.useApp();
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const cancelKey = useRef<string>();
  const cancel = async () => {
    cancelKey.current ??= createRequestId();
    setBusy(true);
    try {
      await taskApi.cancel(id, cancelKey.current);
      cancelKey.current = undefined;
      setRevision(n => n + 1);
      onChanged();
      void message.success("取消请求已处理，最终状态以运行记录为准");
    } catch (err) {
      void message.error(err instanceof Error ? err.message : "操作失败");
      if (err instanceof ApiError && err.status > 0 && err.status < 500) cancelKey.current = undefined;
    } finally { setBusy(false); }
  };
  const load = useCallback(async (signal: AbortSignal) => {
    const run = await taskApi.run(id, signal);
    if (run.task_id !== taskId) throw new Error("该运行不属于当前任务，请重新选择。");
    return { run, revision };
  }, [id, taskId, revision]);
  const { data, error } = useTaskPolling(load, 3000);
  const run = data?.run;
  return <section className="task-run-record" aria-label="本次运行">
    {error && <Alert type="error" showIcon title={error} />}
    {run ? <>
      <Space wrap><h3>本次运行</h3><StatusTag status={run.status} />
        {["queued", "running"].includes(run.status) && <Button loading={busy} disabled={Boolean(run.cancel_requested_at)} onClick={() => modal.confirm({ title: "取消这次运行？", content: "只取消本次执行，不暂停后续计划；已发出的远端请求可能仍在处理，最终状态以后台确认为准。", okText: "确认取消", cancelText: "返回", onOk: cancel })}>{run.cancel_requested_at ? "取消请求已提交" : "取消运行"}</Button>}
      </Space>
      <Descriptions size="small" column={1} items={[
        { key: "model", label: "执行时模型", children: run.config_snapshot?.model_display_name ?? "—" },
        { key: "version", label: "配置版本", children: run.config_snapshot ? `v${run.config_snapshot.task_version}` : "—" },
        { key: "plan", label: "计划时间（北京时间）", children: time(run.scheduled_for) },
        { key: "start", label: "实际开始（北京时间）", children: time(run.started_at) },
        { key: "end", label: "实际结束（北京时间）", children: time(run.finished_at) },
      ]} />
      {(run.error_message || run.error_code) && <Alert type="error" showIcon title={run.error_message || "本次运行未成功完成"} description={run.error_code} />}
      <p className="task-cell-secondary">结果正文与后续追问在任务会话中查看。</p>
      <Collapse items={[{ key: "instruction", label: "本次执行要求", children: <div className="task-instruction">{run.config_snapshot?.definition.instruction ?? "—"}</div> }]} />
    </> : !error && <Empty description="正在读取运行记录" />}
  </section>;
}

export function TaskRunsDrawer({ id, revision, onClose, onChanged }: {
  id: string; revision: number; onClose: () => void; onChanged: () => void;
}) {
  const [page, setPage] = useState(1);
  const [runId, setRunId] = useState<string>();
  const load = useCallback(async (signal: AbortSignal) => {
    const [task, history] = await Promise.all([taskApi.get(id, signal), taskApi.history(id, page, signal)]);
    if (history.items.some(run => run.task_id !== id)) throw new Error("运行记录不属于当前任务，请刷新后重试。");
    return { task, history, revision };
  }, [id, page, revision]);
  const { data, error } = useTaskPolling(load, 3000);
  const selected = runId ?? data?.history.items[0]?.id;
  return <Drawer open title="运行记录" size={720} onClose={onClose}>
    {error && <Alert type="error" showIcon title={error} />}
    {data && <p className="task-history-name">{data.task.name}</p>}
    <section className="task-run-history" aria-label="运行历史">
      <h3>运行历史</h3>
      <Table<TaskRun> size="small" rowKey="id" dataSource={data?.history.items ?? []} loading={!data && !error} scroll={{ x: 360, y: 260 }} locale={{ emptyText: "尚无运行记录" }}
        rowClassName={row => row.id === selected ? "is-selected" : ""}
        pagination={{ ...tablePagination, current: page, total: data?.history.total ?? 0, pageSize: 20, showSizeChanger: false, onChange: n => { setPage(n); setRunId(undefined); } }}
        columns={[
          { title: "创建时间（北京时间）", dataIndex: "created_at", render: (v: string, row) => <Button type="link" aria-label={`查看 ${time(v)} 的运行记录`} onClick={() => setRunId(row.id)}>{time(v)}</Button> },
          { title: "状态", dataIndex: "status", render: v => <StatusTag status={v} /> },
        ]} />
    </section>
    {selected && data && <RunRecord key={selected} id={selected} taskId={id} onChanged={onChanged} />}
  </Drawer>;
}

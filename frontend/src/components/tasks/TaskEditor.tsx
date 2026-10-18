import { Alert, App, Button, Drawer, Form, Input, Select, Space, Switch } from "antd";
import { useRef, useState } from "react";
import { ApiError } from "../../api/client";
import { taskApi, type Task, type TaskDefinition } from "../../api/tasks";
import { createRequestId } from "../../lib/requestId";
import { localToUtc, zonedLocal } from "./schedule";
import { useSession } from "../../state/session";

export function TaskEditor({ task, onClose, onSaved }: { task?: Task; onClose: () => void; onSaved: (id: string, conversationId?: string) => void }) {
  const session = useSession();
  const { message } = App.useApp();
  const [form] = Form.useForm<TaskDefinition & { enabled: boolean; once_local?: string }>();
  const kind = Form.useWatch(["schedule", "kind"], form);
  const [busy, setBusy] = useState<"save" | "run" | null>(null);
  const [error, setError] = useState("");
  const pending = useRef<{ fingerprint: string; key: string } | null>(null);
  const submitting = useRef(false);
  const models = session.modelOptions.filter(m => m.available);
  const defaultModel = models.find(m => m.scope === "global" && m.is_default);
  const submit = async (runNow: boolean) => {
    if (submitting.current) return;
    submitting.current = true;
    let definition: TaskDefinition;
    let enabled: boolean;
    try {
      const { once_local, enabled: formEnabled, ...values } = await form.validateFields();
      enabled = formEnabled;
      const { kind } = values.schedule;
      definition = { ...values,
        schedule: { kind, ...(kind === "once" ? { run_at: localToUtc(once_local ?? "") } : {}),
          ...(["daily", "weekly"].includes(kind) ? { local_time: values.schedule.local_time } : {}),
          ...(kind === "weekly" ? { weekday: values.schedule.weekday } : {}) } };
    } catch (err) { submitting.current = false; if (err instanceof Error) setError(err.message); return; }
    const fingerprint = JSON.stringify({ definition, enabled, runNow });
    if (pending.current?.fingerprint !== fingerprint) pending.current = { fingerprint, key: createRequestId() };
    setBusy(runNow ? "run" : "save"); setError("");
    try {
      const result = await taskApi.save(task?.id, { definition, enabled, expected_version: task?.version, idempotency_key: pending.current.key, run_now: runNow });
      void message.success(runNow ? "任务已保存并进入队列" : "任务已保存");
      onSaved(result.task.id, runNow ? result.task.conversation_id : undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
      if (err instanceof ApiError && err.status > 0 && err.status < 500) pending.current = null;
    } finally { submitting.current = false; setBusy(null); }
  };
  return <Drawer open title={task ? "编辑任务" : "新建任务"} size={540} onClose={() => { if (!busy) onClose(); }} maskClosable={!busy}
    footer={<Space className="task-drawer-footer"><Button disabled={Boolean(busy)} loading={busy === "save"} onClick={() => void submit(false)}>保存</Button><Button type="primary" disabled={Boolean(busy)} loading={busy === "run"} onClick={() => void submit(true)}>保存并运行</Button></Space>}>
    <Alert type="info" showIcon title="任务结果自动进入固定会话" description="可使用已授权的联网搜索；不读取聊天、项目文件或长期记忆。所有计划按北京时间执行；错过不补跑，上一轮未结束则跳过本次，后台可用能力少于普通聊天。" />
    {error && <Alert className="task-alert" type="error" showIcon title={error} />}
    <Form form={form} layout="vertical" disabled={Boolean(busy)} className="task-form" initialValues={task ? { ...task.definition, enabled: task.status === "enabled", once_local: task.definition.schedule.run_at ? zonedLocal(task.definition.schedule.run_at) : undefined } : { name: "", instruction: "", model_id: defaultModel?.id, enabled: true, schedule: { kind: "manual", local_time: "09:00", weekday: 0 } }}>
      <Form.Item label="执行助手"><Input disabled value="MelonClaw" /></Form.Item>
      <Form.Item name="name" label="任务名称" rules={[{ required: true, whitespace: true, max: 120 }]}><Input maxLength={120} /></Form.Item>
      <Form.Item name="instruction" label="任务要求" rules={[{ required: true, whitespace: true, max: 12000 }]}><Input.TextArea rows={7} maxLength={12000} showCount placeholder="说明要研究的问题、输出格式和需要引用的来源" /></Form.Item>
      <Form.Item name="model_id" label="执行模型" rules={[{ required: true }]} extra={!models.length ? "暂无可用模型，请先到拓展 → 模型配置。" : defaultModel ? "默认选择管理员设置的系统默认模型，保存后固定使用所选模型。" : "暂无可用的系统默认模型，请明确选择执行模型或联系管理员设置。"}><Select options={models.map(m => ({ value: m.id, label: m.display_name }))} /></Form.Item>
      <Form.Item name={["schedule", "kind"]} label="执行计划" rules={[{ required: true }]}><Select options={[{ value: "manual", label: "手动运行" }, { value: "once", label: "指定时间一次" }, { value: "daily", label: "每天" }, { value: "weekly", label: "每周" }]} /></Form.Item>
      {kind === "once" && <Form.Item name="once_local" label="运行日期和时间" rules={[{ required: true }]} extra="使用北京时间。"><Input type="datetime-local" step={60} /></Form.Item>}
      {kind === "weekly" && <Form.Item name={["schedule", "weekday"]} label="星期" rules={[{ required: true }]}><Select options={[..."一二三四五六日"].map((label, value) => ({ label: `星期${label}`, value }))} /></Form.Item>}
      {(kind === "daily" || kind === "weekly") && <Form.Item name={["schedule", "local_time"]} label="运行时间" rules={[{ required: true }]} extra="使用北京时间；错过不补跑。"><Input type="time" step={60} /></Form.Item>}
      <Form.Item name="enabled" label="启用任务" valuePropName="checked" extra="暂停仅停止后续调度，已排队/运行的继续；仍可手动运行。"><Switch /></Form.Item>
    </Form>
  </Drawer>;
}

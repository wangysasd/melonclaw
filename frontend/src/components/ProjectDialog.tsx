import { useEffect, useState } from "react";
import { Input, Modal } from "antd";

import { useSession } from "../state/session";

interface ProjectDialogProps {
  open: boolean;
  onClose: () => void;
}

/** 新增项目对话框（对齐旧 project-dialog 交互）。 */
export function ProjectDialog({ open, onClose }: ProjectDialogProps) {
  const session = useSession();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setName("");
      setError(null);
      setSubmitting(false);
    }
  }, [open]);

  const submit = async () => {
    const clean = name.trim();
    if (!clean) {
      setError("项目名称不能为空。");
      return;
    }
    if (clean.length > 120) {
      setError("项目名称不能超过 120 个字符。");
      return;
    }
    setSubmitting(true);
    try {
      const project = await session.createProject(clean);
      if (project) {
        onClose();
        return;
      }
      setError("创建失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="新增项目"
      open={open}
      onCancel={() => (submitting ? undefined : onClose())}
      onOk={() => void submit()}
      okText="创建项目"
      cancelText="取消"
      confirmLoading={submitting}
      okButtonProps={{ disabled: !name.trim() }}
      destroyOnHidden={false}
      mask={{ closable: !submitting }}
      afterClose={() => setError(null)}
    >
      <div className="project-dialog-body">
        <label className="dialog-label" htmlFor="project-name-input">
          项目名称
        </label>
        <Input
          id="project-name-input"
          value={name}
          maxLength={120}
          placeholder="例如：春季工作计划"
          autoComplete="off"
          onChange={(event) => {
            setName(event.target.value);
            if (error) setError(null);
          }}
          onPressEnter={() => void submit()}
          disabled={submitting}
        />
        {error ? (
          <div className="dialog-error" role="alert">
            {error}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

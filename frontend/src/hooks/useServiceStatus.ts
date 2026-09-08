import { deriveRunStatus, useSession, type RunStatus } from "../state/session";
import type { ServiceStatus } from "../types/api";

/** 读取服务状态与运行 pill 状态的便捷 hook。 */
export function useServiceStatus(): {
  status: ServiceStatus | null;
  runStatus: RunStatus;
  errorMessage: string;
} {
  const session = useSession();
  return {
    status: session.status,
    runStatus: deriveRunStatus(session),
    errorMessage: session.status?.message ?? "",
  };
}

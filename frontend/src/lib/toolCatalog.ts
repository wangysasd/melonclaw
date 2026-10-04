import type { IconName } from "../components/Icon";

export type ToolAvailability = "core" | "conditional" | "runtime";

export type ToolCategoryId =
  | "workspace"
  | "compute"
  | "web"
  | "memory"
  | "collaboration"
  | "mcp";

export interface ToolCategory {
  id: ToolCategoryId;
  label: string;
  description: string;
  icon: IconName;
}

export interface ToolDefinition {
  name: string;
  label: string;
  description: string;
  category: ToolCategoryId;
  icon: IconName;
  availability: ToolAvailability;
  availabilityLabel: string;
}

export const TOOL_CATEGORIES: readonly ToolCategory[] = [
  {
    id: "workspace",
    label: "文件与工作区",
    description: "浏览、读取和修改当前项目工作区。",
    icon: "folder",
  },
  {
    id: "compute",
    label: "命令与计算",
    description: "执行受控命令或在解释器中完成计算。",
    icon: "terminal",
  },
  {
    id: "web",
    label: "联网搜索",
    description: "检索外部资料，补充当前任务所需信息。",
    icon: "globe-2",
  },
  {
    id: "memory",
    label: "长期记忆",
    description: "按当前用户、租户和全局范围管理记忆。",
    icon: "brain",
  },
  {
    id: "collaboration",
    label: "协作与提问",
    description: "委派子 Agent，或在需要时向用户提问。",
    icon: "users",
  },
  {
    id: "mcp",
    label: "MCP 外部服务",
    description: "连接外部 MCP 服务，并展示运行时发现的工具。",
    icon: "plug",
  },
] as const;

export const TOOL_CATALOG: readonly ToolDefinition[] = [
  { name: "prepare_mcp_install", label: "预览 MCP 安装", description: "从聊天配置草稿准备个人安装清单，不连接远端。", category: "mcp", icon: "plug", availability: "core", availabilityLabel: "核心工具" },
  { name: "test_mcp_install", label: "测试 MCP 安装", description: "审批后向目标发送凭据并发现工具，不安装。", category: "mcp", icon: "plug", availability: "core", availabilityLabel: "需要审批" },
  { name: "confirm_mcp_install", label: "安装 MCP", description: "审批后安装到自己名下，下一条消息生效。", category: "mcp", icon: "shield-check", availability: "core", availabilityLabel: "需要审批" },
  {
    name: "prepare_skill_creation",
    label: "生成 Skill 预览",
    description: "从聊天提炼正文和文本参考，校验后准备保存清单。",
    category: "workspace",
    icon: "file-text",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "prepare_skill_install",
    label: "预览 Skill 安装",
    description: "从 GitHub 或聊天 ZIP 附件下载、校验并准备安装清单。",
    category: "workspace",
    icon: "file-text",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "confirm_skill_install",
    label: "安装 Skill",
    description: "人工确认安装范围与启用选项后，提交已预览的 Skill。",
    category: "workspace",
    icon: "shield-check",
    availability: "core",
    availabilityLabel: "需要审批",
  },
  {
    name: "ls",
    label: "查看目录",
    description: "列出工作区中的文件和目录。",
    category: "workspace",
    icon: "folder",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "read_file",
    label: "读取文件",
    description: "读取工作区文件内容。",
    category: "workspace",
    icon: "file-text",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "write_file",
    label: "写入文件",
    description: "创建或覆盖工作区文件。",
    category: "workspace",
    icon: "file-pen-line",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "edit_file",
    label: "编辑文件",
    description: "对已有文件执行精确文本编辑。",
    category: "workspace",
    icon: "pencil-line",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "delete",
    label: "删除文件",
    description: "删除工作区中的文件或目录。",
    category: "workspace",
    icon: "trash-2",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "glob",
    label: "查找文件",
    description: "按文件名模式查找工作区文件。",
    category: "workspace",
    icon: "scan-search",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "grep",
    label: "查找内容",
    description: "在文件内容中搜索文本或正则模式。",
    category: "workspace",
    icon: "search",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "execute",
    label: "执行命令",
    description: "在当前工作区执行命令行任务。",
    category: "compute",
    icon: "terminal",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "eval",
    label: "执行计算",
    description: "在 QuickJS 解释器中完成轻量计算。",
    category: "compute",
    icon: "calculator",
    availability: "core",
    availabilityLabel: "解释器能力",
  },
  {
    name: "internet_search",
    label: "搜索资料",
    description: "使用联网搜索获取外部资料。",
    category: "web",
    icon: "globe-2",
    availability: "core",
    availabilityLabel: "项目工具",
  },
  {
    name: "search_memory",
    label: "检索记忆",
    description: "搜索当前运行有权访问的记忆。",
    category: "memory",
    icon: "search",
    availability: "core",
    availabilityLabel: "记忆工具",
  },
  {
    name: "read_memory",
    label: "读取记忆",
    description: "读取指定范围中的记忆条目。",
    category: "memory",
    icon: "book-open",
    availability: "core",
    availabilityLabel: "记忆工具",
  },
  {
    name: "remember_user_memory",
    label: "保存个人记忆",
    description: "保存用户明确要求记住的偏好或背景。",
    category: "memory",
    icon: "bookmark",
    availability: "core",
    availabilityLabel: "记忆工具",
  },
  {
    name: "forget_user_memory",
    label: "删除个人记忆",
    description: "删除用户范围中的记忆条目或精确内容。",
    category: "memory",
    icon: "trash-2",
    availability: "core",
    availabilityLabel: "记忆工具",
  },
  {
    name: "propose_tenant_memory",
    label: "提交租户记忆提案",
    description: "提出租户共享记忆变更，不直接发布内容。",
    category: "memory",
    icon: "users",
    availability: "core",
    availabilityLabel: "记忆工具",
  },
  {
    name: "task",
    label: "委派子 Agent",
    description: "把独立子任务交给通用子 Agent。",
    category: "collaboration",
    icon: "list-checks",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "ask_user",
    label: "向用户提问",
    description: "在缺少关键选择时暂停并收集用户回答。",
    category: "collaboration",
    icon: "message-circle",
    availability: "conditional",
    availabilityLabel: "按客户端能力",
  },
  {
    name: "find_tools",
    label: "查找可用工具",
    description: "按名称或用途检索已授权工具，并在本轮后续调用中启用。",
    category: "collaboration",
    icon: "search",
    availability: "core",
    availabilityLabel: "核心工具",
  },
  {
    name: "list_mcp_tools",
    label: "查看 MCP 工具",
    description: "列出 MCP 服务、发现状态和运行时工具名称。",
    category: "mcp",
    icon: "plug",
    availability: "runtime",
    availabilityLabel: "运行时目录",
  },
] as const;

const TOOL_DEFINITIONS = new Map(
  TOOL_CATALOG.map((definition) => [definition.name, definition]),
);

const COMMAND_TOOL_NAMES = new Set([
  "bash",
  "command",
  "execute",
  "exec",
  "powershell",
  "run_command",
  "run_shell",
  "sh",
  "shell",
  "terminal",
  "zsh",
]);

export function canonicalToolName(name: string): string {
  return name.trim().toLowerCase();
}

export function toolDefinition(name: string): ToolDefinition | undefined {
  return TOOL_DEFINITIONS.get(canonicalToolName(name));
}

export function toolIconName(name: string): IconName {
  const normalized = name.trim().toLowerCase();
  return toolDefinition(normalized)?.icon
    ?? (COMMAND_TOOL_NAMES.has(normalized) ? "terminal" : "wrench");
}

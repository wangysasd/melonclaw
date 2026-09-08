const TOOL_LABELS: Record<string, string> = {
  search: "搜索资料",
  tavily_search: "搜索资料",
  internet_search: "搜索资料",
  read_file: "读取文件",
  write_file: "写入文件",
  edit_file: "编辑文件",
  delete_file: "删除文件",
  glob: "查找文件",
  grep: "查找内容",
  ls: "查看目录",
  execute: "执行命令",
  eval: "执行计算",
  task: "委派子 Agent",
  search_memory: "检索记忆",
  read_memory: "读取记忆",
  remember_user_memory: "保存个人记忆",
  forget_user_memory: "删除个人记忆",
  propose_tenant_memory: "提交租户记忆提案",
};

export function toolSummary(name: string): string {
  return TOOL_LABELS[name] || name || "未知工具";
}


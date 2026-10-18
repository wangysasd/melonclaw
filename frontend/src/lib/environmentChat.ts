/** 原文只用于本次请求；整个环境配置指令从乐观消息和失败回填撤下。 */
export function environmentChatDisplay(content: string): string {
  const stored = /^用户请求维护环境变量 ([A-Za-z_][A-Za-z0-9_]*)；/.exec(content);
  if (stored) return `维护环境变量 ${stored[1]}（值已隐藏）`;
  return /^(?:请帮我|请|帮我|麻烦)?(?:新增|创建|设置|配置|保存|更新|替换|修改|删除|停用|禁用|启用|开启)(?:一个|我的|系统共享的|个人的)?环境变量/.test(content.trimStart())
    ? "[环境变量维护请求已提交；值不显示在聊天中]" : content;
}

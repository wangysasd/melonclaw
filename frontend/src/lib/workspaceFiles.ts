import { assetKey, type AssetRef } from "./resultBlocks";

/** 浏览器引用由受控目录接口产生，不扩展模型成果 JSON 的路径权限。 */
export type FileRef = AssetRef | { workspace_path: string };
export type FileLocation = string | "attachments";

export function workspaceFileRef(path: string): FileRef {
  return path.startsWith("/outputs/") ? { path } : { workspace_path: path };
}
export function fileRefKey(ref: FileRef): string {
  return "workspace_path" in ref ? ref.workspace_path.startsWith("/outputs/") ? ref.workspace_path : `workspace:${ref.workspace_path}` : assetKey(ref);
}
export function fileRefPath(ref: FileRef): string {
  return "workspace_path" in ref ? ref.workspace_path : "path" in ref ? ref.path : `/attachments/${ref.attachment_id}`;
}
export function fileLocation(ref: FileRef): FileLocation {
  if ("attachment_id" in ref) return "attachments";
  const path = fileRefPath(ref);
  return path.slice(0, path.lastIndexOf("/")) || "/";
}

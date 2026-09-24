import { App as AntdApp, ConfigProvider } from "antd";
import XProvider from "@ant-design/x/es/x-provider";
import zhCN from "antd/locale/zh_CN";
import { useEffect, useState } from "react";

import { ChatView } from "./components/ChatView";
import { ProjectDialog } from "./components/ProjectDialog";
import { Sidebar } from "./components/Sidebar";
import { SessionProvider, useSession } from "./state/session";
import { antdTheme } from "./theme/antd";
import "./styles/chat.css";
import "./styles/sidebar.css";

function Workspace() {
  const session = useSession();
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);
  const [moveConversationId, setMoveConversationId] = useState<string | null>(null);

  const openProjectDialog = (conversationId?: string) => {
    setMoveConversationId(conversationId ?? null);
    setProjectDialogOpen(true);
  };

  const closeProjectDialog = () => {
    setProjectDialogOpen(false);
    setMoveConversationId(null);
  };

  const newConversation = () => {
    session.startNewConversation();
  };

  // 全局快捷键：⌘K / Ctrl+K 新建对话。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        newConversation();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.startNewConversation]);

  return (
    <div className="app-shell">
      <Sidebar
        onNewConversation={newConversation}
        onOpenProjectDialog={openProjectDialog}
      />
      <ChatView onOpenProjectDialog={() => openProjectDialog()} />
      <ProjectDialog
        open={projectDialogOpen}
        moveConversationId={moveConversationId}
        onClose={closeProjectDialog}
      />
    </div>
  );
}

export default function App() {
  return (
    <ConfigProvider theme={antdTheme} locale={zhCN}>
      <XProvider theme={antdTheme}>
        <AntdApp>
          <SessionProvider>
            <Workspace />
          </SessionProvider>
        </AntdApp>
      </XProvider>
    </ConfigProvider>
  );
}

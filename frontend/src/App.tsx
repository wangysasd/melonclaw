import { App as AntdApp, ConfigProvider } from "antd";
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

  const newConversation = () => {
    void session.newConversation();
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
  }, [session.newConversation]);

  return (
    <div className="app-shell">
      <Sidebar
        onNewConversation={newConversation}
        onOpenProjectDialog={() => setProjectDialogOpen(true)}
      />
      <ChatView />
      <ProjectDialog
        open={projectDialogOpen}
        onClose={() => setProjectDialogOpen(false)}
      />
    </div>
  );
}

export default function App() {
  return (
    <ConfigProvider theme={antdTheme} locale={zhCN}>
      <AntdApp>
        <SessionProvider>
          <Workspace />
        </SessionProvider>
      </AntdApp>
    </ConfigProvider>
  );
}

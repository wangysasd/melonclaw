import { App as AntdApp, ConfigProvider } from "antd";
import XProvider from "@ant-design/x/es/x-provider";
import zhCN from "antd/locale/zh_CN";
import { useCallback, useEffect, useState } from "react";

import { ChatView } from "./components/ChatView";
import { ProjectDialog } from "./components/ProjectDialog";
import { ResourceView } from "./components/ResourceView";
import { Sidebar } from "./components/Sidebar";
import { SessionProvider, useSession } from "./state/session";
import type { SkillOption } from "./types/api";
import { tokenCss } from "./theme/tokens";
import { antdTheme, overlayStyles } from "./theme/antd";
import "./styles/chat.css";
import "./styles/sidebar.css";

function Workspace() {
  const session = useSession();
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);
  const [moveConversationId, setMoveConversationId] = useState<string | null>(null);
  const [view, setView] = useState<"chat" | "resources">("chat");
  const [resourceTab, setResourceTab] = useState("skills");
  const [initialSkill, setInitialSkill] = useState<SkillOption | null>(null);

  const openProjectDialog = (conversationId?: string) => {
    setMoveConversationId(conversationId ?? null);
    setProjectDialogOpen(true);
  };

  const closeProjectDialog = () => {
    setProjectDialogOpen(false);
    setMoveConversationId(null);
  };

  const newConversation = () => {
    setInitialSkill(null);
    setView("chat");
    session.startNewConversation();
  };

  const clearInitialSkill = useCallback(() => setInitialSkill(null), []);

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
        onOpenResources={() => {
          setResourceTab("skills");
          setView("resources");
        }}
        resourcesActive={view === "resources"}
        onNavigateChat={() => setView("chat")}
      />
      {view === "resources" ? (
        <ResourceView
          initialTab={resourceTab}
          onClose={() => setView("chat")}
          onTrySkill={(skill) => {
            session.startNewConversation();
            setInitialSkill(skill);
            setView("chat");
          }}
        />
      ) : (
        <ChatView
          onOpenProjectDialog={() => openProjectDialog()}
          onOpenModelSettings={() => {
            setResourceTab("models");
            setView("resources");
          }}
          initialSkill={initialSkill}
          onInitialSkillApplied={clearInitialSkill}
        />
      )}
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
    <ConfigProvider theme={antdTheme} locale={zhCN}
      modal={{ styles: { ...overlayStyles, container: { padding: 0 } } }}
      drawer={{ styles: overlayStyles }}>
      <style>{tokenCss}</style>
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

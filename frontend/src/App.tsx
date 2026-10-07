import { AccessNoticeGate } from "./components/ComplianceNotice";
import { AuthGate } from "./components/AuthGate";
import { AccountManagement } from "./components/AccountManagement";
import "./styles/accounts.css";
import { App as AntdApp, ConfigProvider } from "antd";
import XProvider from "@ant-design/x/es/x-provider";
import zhCN from "antd/locale/zh_CN";
import { useCallback, useEffect, useState } from "react";

import { ChatView } from "./components/ChatView";
import { ProjectDialog } from "./components/ProjectDialog";
import { ResourceView } from "./components/ResourceView";
import { GlobalNav, type WorkspaceView } from "./components/GlobalNav";
import { PlaceholderView } from "./components/PlaceholderView";
import { readStorage, writeStorage } from "./state/storage";
import { Sidebar } from "./components/Sidebar";
import { SessionProvider, useSession } from "./state/session";
import type { SkillOption } from "./types/api";
import { tokenCss } from "./theme/tokens";
import { antdTheme, overlayStyles } from "./theme/antd";
import "./styles/chat.css";
import "./styles/sidebar.css";
import "./styles/artifacts.css";

function Workspace() {
  const session = useSession();
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);
  const [moveConversationId, setMoveConversationId] = useState<string | null>(null);
  const [view, setView] = useState<WorkspaceView>("chat");
  const [resourceTab, setResourceTab] = useState(() => { const tab = readStorage("melonclaw.resource_tab.v1"); return tab && ["skills", "mcp", "models"].includes(tab) ? tab : "skills"; });
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
      <GlobalNav view={view} onHome={() => setView("chat")} onResources={() => setView("resources")} onAmp={() => setView("amp")} onMindera={() => setView("mindera")} onAdmin={() => setView("admin")} />
      <div className="home-workspace" hidden={view !== "chat"}>
        <Sidebar key={`sidebar:${session.userId}`} onNewConversation={newConversation} onOpenProjectDialog={openProjectDialog} onNavigateChat={() => setView("chat")} />
        <ChatView
          key={`chat:${session.userId}`}
          active={view === "chat"}
          onOpenProjectDialog={() => openProjectDialog()}
          onOpenModelSettings={() => {
            setResourceTab("models");
            writeStorage("melonclaw.resource_tab.v1", "models");
            setView("resources");
          }}
          initialSkill={initialSkill}
          onInitialSkillApplied={clearInitialSkill}
        />
      </div>
      {view === "resources" ? (
        <ResourceView
          key={session.userId}
          initialTab={resourceTab}
          onTabChange={(tab) => { setResourceTab(tab); writeStorage("melonclaw.resource_tab.v1", tab); }}
          onClose={() => setView("chat")}
          onTrySkill={(skill) => {
            session.startNewConversation();
            setInitialSkill(skill);
            setView("chat");
          }}
        />
      ) : null}
      {view === "admin" && session.userId === "admin" ? <AccountManagement /> : null}
      {view === "amp" ? <PlaceholderView title="AMP" /> : null}
      {view === "mindera" ? <PlaceholderView title="Mindera" /> : null}
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
          <AccessNoticeGate><AuthGate>{(user) => <SessionProvider key={user.user_id} initialUserId={user.user_id}><Workspace /></SessionProvider>}</AuthGate></AccessNoticeGate>
        </AntdApp>
      </XProvider>
    </ConfigProvider>
  );
}

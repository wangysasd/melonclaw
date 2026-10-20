import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/global.css";
import "./styles/resources.css";
import "./styles/management.css";
import "./styles/overlay-buttons.css";
import "./styles/tables.css";
import "./styles/buttons.css";
import "./styles/chat-content.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("找不到 #root 挂载点。");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

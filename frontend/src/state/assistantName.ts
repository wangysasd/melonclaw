import { createContext, useContext } from "react";

export const AssistantNameContext = createContext("");
export const useAssistantName = () => useContext(AssistantNameContext);

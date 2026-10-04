import { createContext, useContext } from "react";
import type { FileRef } from "../lib/workspaceFiles";
import type { ResultFileMetadata } from "../api/results";

export type ArtifactControls = {
  openArtifact: (ref: FileRef, messageId?: string, conversationId?: string) => void;
  openList: () => void;
  getMetadata: (ref: FileRef) => Promise<ResultFileMetadata>;
  count: number;
  open: boolean;
};
export const ArtifactContext = createContext<ArtifactControls | null>(null);
export function useArtifacts() { return useContext(ArtifactContext); }

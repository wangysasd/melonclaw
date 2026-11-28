import type { ReactNode } from "react";
import { PRIVACY_PATH } from "../lib/privacyMail";

export function SmsPurposeNote({ children, onOpen }: { children: ReactNode; onOpen?: () => void }) {
  return (
    <p className="auth-note">
      {children}{" "}
      <a href={PRIVACY_PATH} onClick={onOpen}>隐私说明</a>
    </p>
  );
}

interface AppLogoProps {
  name?: string | null;
}

const logoColors = [
  { background: "#EEF4FF", color: "#315EAA", border: "#D5E2FA" },
  { background: "#F5F0FA", color: "#74529A", border: "#E5D8F1" },
  { background: "#EDF7F6", color: "#32776F", border: "#D3EAE7" },
  { background: "#FBF4E9", color: "#8D6529", border: "#EFE0C6" },
  { background: "#F0F6EE", color: "#4A7043", border: "#DCE9D6" },
] as const;

function hashString(value: string): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (Math.imul(hash, 31) + value.charCodeAt(index)) | 0;
  }
  return Math.abs(hash) % logoColors.length;
}

export function AppLogo({ name }: AppLogoProps) {
  const value = name?.trim() ?? "";
  const text = value ? Array.from(value)[0].toUpperCase() : "?";
  const colors = logoColors[hashString(value.toLowerCase())];

  return (
    <span
      className="app-logo"
      aria-hidden="true"
      style={{
        backgroundColor: colors.background,
        color: colors.color,
        borderColor: colors.border,
      }}
    >
      {text}
    </span>
  );
}

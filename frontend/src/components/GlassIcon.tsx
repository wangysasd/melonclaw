/** Decorative local artwork; the containing control supplies its accessible name. */
export function GlassIcon({ name, className = "" }: {
  name: "home" | "clock" | "layers" | "chart" | "document" | "globe";
  className?: string;
}) {
  return <img className={`glass-icon ${className}`} src={`/assets/glass/${name}.webp`}
    width={40} height={40} alt="" aria-hidden="true" draggable={false} decoding="async" />;
}

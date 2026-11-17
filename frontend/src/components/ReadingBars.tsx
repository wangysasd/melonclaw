/** 朗读按钮的加载扫光与播放波形。减少动效时保持静止。 */
export function ReadingBars({ mode }: { mode: "loading" | "playing" }) {
  return (
    <span className={`read-aloud-bars is-${mode}`} aria-hidden="true">
      <span />
      <span />
      <span />
      <span />
    </span>
  );
}

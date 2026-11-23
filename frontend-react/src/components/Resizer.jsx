// Drag handle between panels, like the split bars in VS Code.
// Drag to resize, double-click to reset, arrow keys when focused.
export default function Resizer({ side, label, getWidth, onChange, min, max }) {
  // side="left": handle sits on the right edge of a left panel (dragging right grows it)
  // side="right": handle sits on the left edge of a right panel (dragging left grows it)
  const dir = side === "left" ? 1 : -1;
  const clamp = (w) => Math.round(Math.min(Math.max(w, min), max()));

  const onPointerDown = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const handle = e.currentTarget;
    const startX = e.clientX;
    const startW = getWidth();
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("resizing");

    const move = (ev) => onChange(clamp(startW + (ev.clientX - startX) * dir));
    const up = () => {
      handle.classList.remove("dragging");
      document.body.classList.remove("resizing");
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  };

  const onKeyDown = (e) => {
    const step = e.shiftKey ? 48 : 16;
    if (e.key === "ArrowLeft") onChange(clamp(getWidth() - step * dir));
    else if (e.key === "ArrowRight") onChange(clamp(getWidth() + step * dir));
    else return;
    e.preventDefault();
  };

  return (
    <div
      className={`resizer resizer-${side}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      tabIndex={0}
      title="Drag to resize · double-click to reset"
      onPointerDown={onPointerDown}
      onDoubleClick={() => onChange(null)}
      onKeyDown={onKeyDown}
    />
  );
}

import {useCallback, useRef, type CSSProperties, type ReactNode} from "react";

type Props = Readonly<{
  primary: ReactNode;
  secondary: ReactNode;
  secondaryWidth: number;
  minimum: number;
  maximum: number;
  onWidthChange(width: number): void;
}>;

export function SplitPane({primary, secondary, secondaryWidth, minimum, maximum, onWidthChange}: Props) {
  const frame = useRef<HTMLDivElement>(null);
  const clamp = useCallback((width: number) => Math.max(minimum, Math.min(maximum, width)), [maximum, minimum]);
  const startResize = (event: React.PointerEvent<HTMLDivElement>) => {
    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);
    const right = frame.current?.getBoundingClientRect().right ?? window.innerWidth;
    const move = (next: PointerEvent) => onWidthChange(clamp(right - next.clientX));
    const end = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", end);
      target.removeEventListener("pointercancel", end);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", end);
    target.addEventListener("pointercancel", end);
  };
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowLeft") onWidthChange(clamp(secondaryWidth + (event.shiftKey ? 48 : 12)));
    else if (event.key === "ArrowRight") onWidthChange(clamp(secondaryWidth - (event.shiftKey ? 48 : 12)));
    else if (event.key === "Home") onWidthChange(minimum);
    else if (event.key === "End") onWidthChange(maximum);
    else return;
    event.preventDefault();
  };
  const style = {"--secondary-width": `${secondaryWidth}px`} as CSSProperties;
  return <div className="gf-split-pane" ref={frame} style={style}>
    <div className="gf-split-primary">{primary}</div>
    <div className="gf-splitter" role="separator" aria-orientation="vertical" aria-valuemin={minimum} aria-valuemax={maximum} aria-valuenow={secondaryWidth} tabIndex={0} onPointerDown={startResize} onKeyDown={onKeyDown} />
    <aside className="gf-split-secondary">{secondary}</aside>
  </div>;
}

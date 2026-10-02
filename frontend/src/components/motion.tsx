/*
 * Spring motion primitives on @react-spring/web — the app's only runtime animation dependency.
 * Enter/reveal animations never handle unmount (parents conditionally render; exit animation would
 * need a mount-protocol change everywhere). All of it collapses to instant under
 * prefers-reduced-motion.
 */
import { useEffect, useState, type ReactNode } from "react";
import { animated, useSpring, useSpringValue } from "@react-spring/web";

export const prefersReducedMotion = () =>
  typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** A number that springs to each new value instead of snapping. `format` renders every frame. */
export function Ticker({ value, format }: { value: number; format: (v: number) => string }) {
  const [display, setDisplay] = useState(() => format(value));
  const spring = useSpringValue(value, {
    config: { tension: 180, friction: 26 },
    immediate: prefersReducedMotion(),
    onChange: (r: { value: number }) => setDisplay(format(r.value)),
  });
  useEffect(() => {
    spring.start(value);
    return () => { spring.stop(); };
  }, [value, spring]);
  return <span>{display}</span>;
}

/** One-shot entrance: a small rise-and-fade so a card arriving communicates itself. */
export function Reveal({ children, delay = 0, className, style }: {
  children: ReactNode; delay?: number; className?: string; style?: React.CSSProperties;
}) {
  const styles = useSpring({
    from: { opacity: 0, y: 10 },
    to: { opacity: 1, y: 0 },
    delay,
    immediate: prefersReducedMotion(),
    config: { tension: 170, friction: 22 },
  });
  return <animated.div className={className} style={{ ...style, opacity: styles.opacity, y: styles.y }}>{children}</animated.div>;
}

import { useRef, useState, useLayoutEffect, type ReactNode, type CSSProperties } from 'react';

interface MarqueeTextProps {
  children: ReactNode;
  className?: string;
  /** Approx scrolling speed in pixels-per-second. Higher = snappier.
   *  The math: one ping-pong cycle is two slides of (overflow_amount)
   *  each, plus two short pauses. At 100 px/s, a 200px overflow
   *  cycles in about 6s total — readable without being frenetic. */
  speed?: number;
}

// Filename ping-pong marquee — only animates when the text actually
// overflows its container (measured via ResizeObserver). Used by the
// file/episode row cells for long release names.
export function MarqueeText({ children, className, speed = 100 }: MarqueeTextProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLSpanElement>(null);
  const [overflows, setOverflows] = useState(false);
  const [durationSec, setDurationSec] = useState(8);
  const [shiftPx, setShiftPx] = useState(0);

  useLayoutEffect(() => {
    const container = containerRef.current;
    const inner = innerRef.current;
    if (!container || !inner) return;

    const check = () => {
      const naturalWidth = inner.scrollWidth;
      const visibleWidth = container.clientWidth;
      const overflowAmount = naturalWidth - visibleWidth;
      const isOverflow = overflowAmount > 2;
      setOverflows(isOverflow);
      if (isOverflow) {
        // Negative shift: translateX(shiftPx) moves the inner LEFT
        // by exactly the overflow amount so the END of the text
        // aligns with the right edge of the container. The small
        // -4px padding gives a touch of breathing room so the last
        // character isn't kissing the container border.
        setShiftPx(-(overflowAmount + 4));
        // Cycle math: two slides of overflowAmount + 1.4s of pauses.
        // Slide time = overflowAmount / speed each direction.
        const slideTimeSec = overflowAmount / speed;
        const totalCycle = (slideTimeSec * 2) + 1.4;
        // Floor at 4s so tiny overflows still have a visible pause
        // rhythm; ceiling at 18s so absurd-length text doesn't
        // demand the user wait forever for the other end.
        const d = Math.max(4, Math.min(18, totalCycle));
        setDurationSec(d);
      }
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(container);
    ro.observe(inner);
    return () => ro.disconnect();
  }, [children, speed]);

  return (
    <div ref={containerRef} className={`marquee-outer ${className ?? ''}`}>
      <span
        ref={innerRef}
        className={overflows ? 'marquee-inner scrolling' : 'marquee-inner'}
        style={overflows ? {
          ['--marquee-duration' as never]: `${durationSec}s`,
          ['--marquee-shift' as never]: `${shiftPx}px`,
        } as CSSProperties : undefined}
      >
        {children}
      </span>
    </div>
  );
}

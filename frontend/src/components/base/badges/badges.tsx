import type { ReactNode } from "react";
import { cx, sortCx } from "@/utils/cx";

export const dotColors = sortCx({
    brand: "text-accent",
    success: "text-conf-high",
    warning: "text-conf-mid",
    error: "text-conf-low",
    gray: "text-ink-soft",
});

export interface BadgeWithDotProps {
    /** Dot color — maps to the accent/confidence palette. */
    color?: keyof typeof dotColors;
    /** Adds a pulsing "ping" halo behind the dot for a live/active state. */
    pulse?: boolean;
    children: ReactNode;
    className?: string;
}

/**
 * Untitled UI badge: a small neutral pill for inline tags (e.g. media-type
 * chips). Tint the text/background via `className` when needed.
 */
export const Badge = ({ children, className }: { children: ReactNode; className?: string }) => {
    return (
        <span
            className={cx(
                "inline-flex items-center rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.04em] text-ink-soft",
                className,
            )}
        >
            {children}
        </span>
    );
};

/**
 * Untitled UI badge-with-dot: a glassy pill with a leading status dot. The dot
 * carries the color; the label stays neutral. `pulse` adds the live ping halo.
 */
export const BadgeWithDot = ({ color = "gray", pulse, children, className }: BadgeWithDotProps) => {
    return (
        <span
            className={cx(
                "inline-flex items-center gap-2 rounded-full border border-line bg-glass px-3 py-1 text-xs font-medium text-ink-muted backdrop-blur",
                className,
            )}
        >
            <span className={cx("relative flex size-1.5", dotColors[color])}>
                {pulse ? <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" /> : null}
                <span className="relative inline-flex size-1.5 rounded-full bg-current" />
            </span>
            {children}
        </span>
    );
};

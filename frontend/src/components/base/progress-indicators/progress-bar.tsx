import { cx } from "@/utils/cx";

export interface ProgressBarProps {
    /** Current value. */
    value: number;
    /** Value that represents a full bar. Defaults to 100. */
    max?: number;
    /** Fill color (any CSS color). Defaults to the emerald accent. */
    color?: string;
    /** Track height in px. Defaults to 6. */
    height?: number;
    /** Minimum rendered width (%) when value > 0, so tiny values stay visible. */
    minVisible?: number;
    /** Animated indeterminate sweep for "working, total unknown" states. */
    indeterminate?: boolean;
    /** Accessible name announced by screen readers — essential for the
     *  indeterminate bar, which has no numeric value to read out. */
    label?: string;
    /** Class for the outer track element. */
    className?: string;
}

/**
 * Untitled UI progress bar: a rounded track with an animated fill. Color is a
 * free CSS value so callers can tint per media type; defaults to the accent.
 * Pass `indeterminate` for a sliding sweep when there's no known total.
 */
export const ProgressBar = ({
    value,
    max = 100,
    color = "var(--accent)",
    height = 6,
    minVisible = 0,
    indeterminate = false,
    label = "Loading",
    className,
}: ProgressBarProps) => {
    const ratio = max > 0 ? value / max : 0;
    const pct = value > 0 ? Math.max(minVisible, Math.min(100, ratio * 100)) : 0;

    if (indeterminate) {
        return (
            <div className={cx("relative overflow-hidden rounded-full bg-white/[0.06]", className)} style={{ height }} role="progressbar" aria-label={label} aria-busy={true}>
                <div
                    className="absolute inset-y-0 w-2/5 rounded-full"
                    style={{ background: color, opacity: 0.85, animation: "kira-indeterminate 1.1s ease-in-out infinite" }}
                />
            </div>
        );
    }

    return (
        <div
            className={cx("overflow-hidden rounded-full bg-white/[0.06]", className)}
            style={{ height }}
            role="progressbar"
            aria-label={label}
            aria-valuenow={value}
            aria-valuemin={0}
            aria-valuemax={max}
        >
            <div
                className="h-full rounded-full transition-[width] duration-700 ease-out"
                style={{ width: `${pct}%`, background: color, opacity: 0.85 }}
            />
        </div>
    );
};

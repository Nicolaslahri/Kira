import type { ReactNode } from "react";
import { cx } from "@/utils/cx";
import { FeaturedIcon } from "../featured-icons/featured-icon";

// Untitled UI metric card — the canonical KPI tile (featured icon, label, big
// value, supporting line). Built from UUI base components + tokens. Renders a
// <button> when `onClick` is given (keyboard/AT operable) with a reveal arrow,
// otherwise a static <div>. Dark-theme elevation: `bg-secondary` sits above the
// near-black page, with a `ring-secondary` hairline — the UUI card recipe.

export interface MetricCardProps {
    /** Featured icon glyph. */
    icon: ReactNode;
    /** Featured-icon color theme. */
    color?: "brand" | "success" | "warning" | "error" | "gray";
    /** Custom icon colour (a brand/media hex) — overrides `color`. */
    tint?: string;
    /** Uppercase eyebrow label. */
    label: string;
    /** Headline value (number / CountUp). */
    value: ReactNode;
    /** Supporting line under the value. */
    sub?: ReactNode;
    /** Makes the whole card a button + shows a reveal arrow. */
    onClick?: () => void;
    className?: string;
}

export const MetricCard = ({ icon, color = "brand", tint, label, value, sub, onClick, className }: MetricCardProps) => {
    const root = cx(
        "group relative flex flex-col rounded-xl bg-secondary p-5 text-left shadow-xs ring-1 ring-secondary transition duration-200 ease-linear",
        onClick && "cursor-pointer hover:bg-tertiary hover:ring-primary",
        className,
    );

    const content = (
        <>
            <div className="flex items-center justify-between">
                <FeaturedIcon size="md" color={color} tint={tint} icon={icon} />
                {onClick ? (
                    <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" className="size-4 -translate-x-1 text-fg-quaternary opacity-0 transition-all duration-200 group-hover:translate-x-0 group-hover:opacity-100">
                        <path d="M4.167 10h11.666m0 0L10 4.167M15.833 10 10 15.833" stroke="currentColor" strokeWidth="1.67" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                ) : null}
            </div>
            <p className="mt-4 text-xs font-medium uppercase tracking-[0.08em] text-tertiary">{label}</p>
            <p className="mt-1 text-3xl font-semibold leading-none tracking-tight text-primary tabular-nums">{value}</p>
            {sub ? <div className="mt-2 text-sm text-tertiary">{sub}</div> : null}
        </>
    );

    if (onClick) {
        return (
            <button type="button" onClick={onClick} className={root}>
                {content}
            </button>
        );
    }
    return <div className={root}>{content}</div>;
};

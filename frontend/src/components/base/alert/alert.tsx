import type { FC, ReactNode } from "react";
import { isValidElement } from "react";
import { cx, sortCx } from "@/utils/cx";
import { isReactComponent } from "@/utils/is-react-component";

export const styles = sortCx({
    info: "border-line bg-glass [&_[data-alert-icon]]:text-ink-soft",
    warning: "border-[rgba(255,201,74,0.28)] bg-[var(--conf-mid-bg)] [&_[data-alert-icon]]:text-conf-mid",
    error: "border-[rgba(255,91,110,0.28)] bg-[var(--conf-low-bg)] [&_[data-alert-icon]]:text-conf-low",
    success: "border-accent-line bg-[var(--conf-high-bg)] [&_[data-alert-icon]]:text-conf-high",
});

export interface AlertProps {
    /** Color theme — maps to the confidence/accent palette. */
    color?: keyof typeof styles;
    /** Optional leading icon component or element. */
    icon?: FC<{ className?: string }> | ReactNode;
    /** Bold title line above the body. */
    title?: ReactNode;
    children?: ReactNode;
    className?: string;
}

/**
 * Untitled UI alert/callout: a tinted rounded panel with an optional icon and
 * title. Used for provider warnings, ban countdowns, and fallback hints.
 */
export const Alert = ({ color = "info", icon: Icon, title, children, className }: AlertProps) => {
    return (
        <div
            role="status"
            className={cx(
                "flex items-start gap-2.5 rounded-xl border px-3 py-2.5 text-[12.5px] leading-relaxed text-ink-muted",
                styles[color],
                className,
            )}
        >
            {Icon ? (
                <span data-alert-icon className="mt-0.5 shrink-0 [&_svg]:size-4">
                    {isValidElement(Icon) && Icon}
                    {isReactComponent(Icon) && <Icon />}
                </span>
            ) : null}
            <div className="min-w-0 flex-1">
                {title ? <div className="mb-0.5 font-semibold text-ink">{title}</div> : null}
                {children}
            </div>
        </div>
    );
};

import type { FC, ReactNode } from "react";
import { isValidElement } from "react";
import { cx, sortCx } from "@/utils/cx";
import { isReactComponent } from "@/utils/is-react-component";

export const styles = sortCx({
    info: "border-secondary bg-white/[0.04] [&_[data-alert-icon]]:text-tertiary",
    warning: "border-[var(--conf-mid-32)] bg-warning-secondary [&_[data-alert-icon]]:text-warning-primary",
    error: "border-[var(--conf-low-32)] bg-error-secondary [&_[data-alert-icon]]:text-error-primary",
    success: "border-brand bg-success-secondary [&_[data-alert-icon]]:text-success-primary",
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
                "flex items-start gap-2.5 rounded-xl border px-3 py-2.5 text-[12.5px] leading-relaxed text-secondary",
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
                {title ? <div className="mb-0.5 font-semibold text-primary">{title}</div> : null}
                {children}
            </div>
        </div>
    );
};

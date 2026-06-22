import type { MouseEventHandler, ReactNode } from "react";
import { Link as AriaLink } from "react-aria-components";
import { cx, sortCx } from "@/utils/cx";

// Untitled UI sidebar nav item (`NavItemBase`), vendored for Kira and adapted to
// fit a state-based app:
//  • Kira navigates by React state, not URLs — so when no `href` is given this
//    renders a <button> with `onClick` (instead of react-aria `Link`).
//  • A string/number `badge` renders a small count pill locally (no dependency
//    on the full UUI Badge); any other node renders as-is.
//  • `icon` takes a rendered element (Kira's `Ic*` glyphs) rather than a
//    component, sized via `[&_svg]:size-5`.
// Styling is the UUI original — flat `bg-secondary` selected state, hover fill —
// which the dark-theme "Kira look bridge" renders on Kira's palette.

const styles = sortCx({
    root: "group/item relative flex max-h-9 w-full cursor-pointer items-center rounded-md outline-brand transition duration-100 ease-linear select-none hover:bg-primary_hover focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-offset-2",
    rootSelected: "bg-[var(--accent-8)] ring-1 ring-inset ring-[var(--accent-line)]",
});

export interface NavItemBaseProps {
    /** Indented child row (no icon) vs a top-level row. @default "link" */
    type?: "link" | "child";
    /** When set, renders an `<a>` (react-aria Link); otherwise a `<button>`. */
    href?: string;
    /** Leading icon element (top-level rows). */
    icon?: ReactNode;
    /** Trailing slot — a string/number becomes a count pill; any node renders as-is. */
    badge?: ReactNode;
    /** Selected (current page) state. */
    current?: boolean;
    /** Truncate the label. @default true */
    truncate?: boolean;
    onClick?: MouseEventHandler;
    title?: string;
    "aria-label"?: string;
    children?: ReactNode;
}

export const NavItemBase = ({ current, type = "link", badge, href, icon, children, truncate = true, onClick, ...rest }: NavItemBaseProps) => {
    const isChild = type === "child";

    const iconElement = icon && (
        <span
            className={cx(
                "mr-2 inline-flex size-5 shrink-0 items-center justify-center text-fg-quaternary transition-inherit-all group-hover/item:text-fg-quaternary_hover [&_svg]:size-5",
                current && "text-[var(--accent-bright)] group-hover/item:text-[var(--accent-bright)]",
            )}
        >
            {icon}
        </span>
    );

    const badgeElement =
        badge != null && (typeof badge === "string" || typeof badge === "number") ? (
            <span className={cx(
                "ml-auto inline-flex items-center rounded-full px-1.5 py-0.5 text-[11px] font-semibold tabular-nums ring-1 ring-inset",
                current ? "bg-[var(--accent-16)] text-[var(--accent-bright)] ring-[var(--accent-32)]" : "bg-tertiary text-tertiary ring-secondary",
            )}>
                {badge}
            </span>
        ) : (
            badge
        );

    const labelElement = (
        <span
            className={cx(
                "flex-1 text-left text-sm font-semibold text-secondary transition-inherit-all group-hover/item:text-secondary_hover",
                truncate && "truncate",
                current && "text-primary group-hover/item:text-primary",
            )}
        >
            {children}
        </span>
    );

    const className = cx(
        isChild ? "py-2 pr-3 pl-10" : "p-2",
        styles.root,
        current && styles.rootSelected,
        current && (isChild ? "shadow-[inset_2px_0_0_var(--accent)] hover:bg-[var(--accent-12)]" : "shadow-[inset_3px_0_0_var(--accent)] hover:bg-[var(--accent-12)]"),
    );

    const inner = (
        <>
            {iconElement}
            {labelElement}
            {badgeElement}
        </>
    );

    if (href) {
        return (
            <AriaLink href={href} className={className} onClick={onClick} aria-current={current ? "page" : undefined} {...rest}>
                {inner}
            </AriaLink>
        );
    }

    return (
        <button type="button" className={className} onClick={onClick} aria-current={current ? "page" : undefined} {...rest}>
            {inner}
        </button>
    );
};

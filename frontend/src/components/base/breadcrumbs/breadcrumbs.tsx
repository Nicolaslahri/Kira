import { Fragment, type ReactNode } from "react";
import { motion } from "motion/react";
import { cx } from "@/utils/cx";

export interface BreadcrumbItem {
    /** Crumb label. */
    label: ReactNode;
    /** Stable key for the animated leaf (defaults to the label when it's a string). */
    id?: string;
    /** Renders the crumb as a button. */
    onClick?: () => void;
    /** Renders the crumb as a link. */
    href?: string;
}

export interface BreadcrumbsProps {
    items: BreadcrumbItem[];
    /** Separator between crumbs. @default "/" */
    separator?: ReactNode;
    className?: string;
}

/**
 * Untitled UI breadcrumb trail. The current (last) crumb animates in whenever it
 * changes — Kira's signature topbar leaf, kept here so every breadcrumb gets it.
 * Intermediate crumbs become buttons/links when given `onClick`/`href`.
 */
export const Breadcrumbs = ({ items, separator = "/", className }: BreadcrumbsProps) => {
    return (
        <nav aria-label="Breadcrumb" className={cx("flex items-center text-[13px] text-tertiary", className)}>
            {items.map((item, i) => {
                const isLast = i === items.length - 1;
                const key = item.id ?? (typeof item.label === "string" ? item.label : i);
                return (
                    <Fragment key={key}>
                        {i > 0 && (
                            <span aria-hidden="true" className="mx-2 text-quaternary">
                                {separator}
                            </span>
                        )}
                        {isLast ? (
                            <motion.span
                                key={typeof item.label === "string" ? item.label : i}
                                aria-current="page"
                                initial={{ opacity: 0, y: -4 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
                                className="font-semibold text-secondary"
                            >
                                {item.label}
                            </motion.span>
                        ) : item.href ? (
                            <a href={item.href} className="rounded-sm transition-colors hover:text-secondary">
                                {item.label}
                            </a>
                        ) : item.onClick ? (
                            <button type="button" onClick={item.onClick} className="rounded-sm transition-colors hover:text-secondary">
                                {item.label}
                            </button>
                        ) : (
                            <span>{item.label}</span>
                        )}
                    </Fragment>
                );
            })}
        </nav>
    );
};

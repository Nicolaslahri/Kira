import type { ReactNode } from "react";
import { cx } from "@/utils/cx";
import { FeaturedIcon } from "../featured-icons/featured-icon";

export interface ActivityFeedItem {
    id: string;
    /** Node glyph. */
    icon: ReactNode;
    /** Node color theme. */
    color?: "brand" | "success" | "warning" | "error" | "gray";
    /** Primary line (rich text allowed). */
    text: ReactNode;
    /** Relative timestamp line. */
    time: string;
}

/**
 * Untitled UI activity feed — a vertical timeline of FeaturedIcon nodes joined
 * by a hairline connector, each with a line of text and a relative timestamp.
 * Composed from UUI base components + tokens.
 */
export const ActivityFeed = ({ items, className }: { items: ActivityFeedItem[]; className?: string }) => {
    return (
        <ol className={cx("flex flex-col", className)}>
            {items.map((item, i) => {
                const last = i === items.length - 1;
                return (
                    <li key={item.id} className="relative flex gap-3 pb-5 last:pb-0">
                        {/* Connector — runs from below this node to the next. */}
                        {!last ? <span aria-hidden="true" className="absolute top-7 bottom-0 left-3.5 -ml-px w-px bg-border-secondary" /> : null}
                        <FeaturedIcon size="sm" color={item.color ?? "gray"} icon={item.icon} className="relative z-10" />
                        <div className="min-w-0 flex-1 pt-0.5">
                            <div className="truncate text-[13px] leading-snug text-secondary">{item.text}</div>
                            <div className="mt-0.5 text-[11px] text-quaternary">{item.time}</div>
                        </div>
                    </li>
                );
            })}
        </ol>
    );
};

import type { FC, ReactNode } from "react";
import { isValidElement } from "react";
import { cx, sortCx } from "@/utils/cx";
import { isReactComponent } from "@/utils/is-react-component";

export const styles = sortCx({
    sizes: {
        sm: "size-7 rounded-md [&_svg]:size-[13px]",
        md: "size-9 rounded-lg [&_svg]:size-4",
        lg: "size-11 rounded-xl [&_svg]:size-5",
    },
    colors: {
        brand: "bg-brand-secondary text-fg-brand-primary",
        success: "bg-success-secondary text-fg-success-primary",
        warning: "bg-warning-secondary text-fg-warning-primary",
        error: "bg-error-secondary text-fg-error-primary",
        gray: "bg-white/[0.08] text-fg-secondary",
    },
});

export interface FeaturedIconProps {
    /** Icon component or element rendered inside the chip. */
    icon?: FC<{ className?: string }> | ReactNode;
    /** Color theme — maps to the app's confidence/accent tokens. */
    color?: keyof typeof styles.colors;
    /** Size of the chip. */
    size?: keyof typeof styles.sizes;
    /** Custom CSS color (e.g. a provider brand hex). Overrides `color`,
     *  tinting both the background (~12%) and the icon. */
    tint?: string;
    className?: string;
    children?: ReactNode;
}

/**
 * Untitled UI "Featured icon": a tinted rounded chip wrapping a single icon.
 * Uses UUI semantic tokens (brand / success / warning / error); Kira's dark
 * theme re-points those at the confidence palette + emerald accent (see the
 * "Kira look bridge" in theme.css), so brand reads emerald and success/error/
 * warning track confidence. Pass `tint` for an arbitrary brand color.
 */
export const FeaturedIcon = ({ icon: Icon, color = "brand", size = "sm", tint, className, children }: FeaturedIconProps) => {
    return (
        <span
            className={cx("grid shrink-0 place-items-center", styles.sizes[size], !tint && styles.colors[color], className)}
            // color-mix (not `${tint}1f`) so `tint` accepts a `var(--token)` —
            // string-concatenating an alpha onto a var() would be invalid CSS.
            style={tint ? { backgroundColor: `color-mix(in srgb, ${tint} 12%, transparent)`, color: tint } : undefined}
        >
            {isValidElement(Icon) && Icon}
            {isReactComponent(Icon) && <Icon />}
            {children}
        </span>
    );
};

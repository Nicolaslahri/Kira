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
        brand: "bg-accent-soft text-accent",
        success: "bg-[var(--conf-high-bg)] text-conf-high",
        warning: "bg-[var(--conf-mid-bg)] text-conf-mid",
        error: "bg-[var(--conf-low-bg)] text-conf-low",
        gray: "bg-white/[0.08] text-ink-muted",
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
 * Untitled UI "Featured icon" (light theme): a tinted rounded chip wrapping a
 * single icon. Re-skinned to Kira's tokens so success/error/warning track the
 * confidence palette and brand tracks the emerald accent. Pass `tint` for an
 * arbitrary brand color.
 */
export const FeaturedIcon = ({ icon: Icon, color = "brand", size = "sm", tint, className, children }: FeaturedIconProps) => {
    return (
        <span
            className={cx("grid shrink-0 place-items-center", styles.sizes[size], !tint && styles.colors[color], className)}
            style={tint ? { backgroundColor: `${tint}1f`, color: tint } : undefined}
        >
            {isValidElement(Icon) && Icon}
            {isReactComponent(Icon) && <Icon />}
            {children}
        </span>
    );
};

import { type ButtonHTMLAttributes, type DetailedHTMLProps, type FC, type HTMLAttributes, type ReactNode, createContext, isValidElement, useContext } from "react";
import { cx, sortCx } from "@/utils/cx";
import { isReactComponent } from "@/utils/is-react-component";

// Untitled UI ButtonGroup — a connected (shared-border) segmented row of
// actions. Vendored from the starter kit and adapted for Kira:
//  • Built on plain <button>s (role="button") instead of the starter's
//    React-Aria `ToggleButtonGroup`, which gave items `role="radio"` — wrong
//    semantics for an action toolbar (these aren't a single-choice selection).
//  • Adds a `color` per item (gray / primary / destructive) so the primary +
//    destructive actions keep their emphasis inside the group.
// Styled with UUI tokens; `bg-primary` matches the existing `Button` colors.

const sizes = sortCx({
    sm: "gap-1.5 px-3 py-2 text-sm first:rounded-l-lg last:rounded-r-lg *:data-icon:size-4 data-icon-leading:pl-2.5 data-icon-only:px-2",
    md: "gap-1.5 px-3.5 py-2.5 text-sm first:rounded-l-lg last:rounded-r-lg *:data-icon:size-5 data-icon-leading:pl-3 data-icon-only:px-2.5",
    lg: "gap-2 px-4 py-2.5 text-md first:rounded-l-lg last:rounded-r-lg *:data-icon:size-5 data-icon-leading:pl-3.5 data-icon-only:px-3",
});

// Uniform neutral fill/border for every segment (the bg + ring live in
// `common`); `color` only tints the text + icon, so Reject/Approve read as
// red/emerald *text* without colored fills or borders breaking the bar.
const colors = sortCx({
    gray: "text-secondary hover:text-secondary_hover *:data-icon:text-fg-quaternary hover:*:data-icon:text-fg-quaternary_hover",
    primary: "text-[var(--color-fg-brand-primary)] *:data-icon:text-[var(--color-fg-brand-primary)]",
    // success = green (approve) — maps to --conf-high via the bridge. Kept
    // separate from `primary` (the white brand accent) so Approve reads green
    // while other primary actions (Sync, Re-identify) stay neutral.
    success: "text-[var(--color-fg-success-primary)] *:data-icon:text-[var(--color-fg-success-primary)]",
    destructive: "text-error-primary *:data-icon:text-fg-error-primary",
});

type ButtonSize = keyof typeof sizes;
type ButtonColor = keyof typeof colors;

const ButtonGroupContext = createContext<{ size: ButtonSize }>({ size: "md" });

const common =
    "relative inline-flex h-max cursor-pointer items-center justify-center bg-primary font-semibold whitespace-nowrap ring-1 ring-primary ring-inset outline-brand transition duration-100 ease-linear pressed:scale-[0.98] hover:bg-primary_hover focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50 *:data-icon:pointer-events-none *:data-icon:shrink-0 *:data-icon:transition-inherit-all";

export interface ButtonGroupItemProps extends Omit<DetailedHTMLProps<ButtonHTMLAttributes<HTMLButtonElement>, HTMLButtonElement>, "color"> {
    iconLeading?: FC<{ className?: string }> | ReactNode;
    iconTrailing?: FC<{ className?: string }> | ReactNode;
    /** Segment emphasis. @default "gray" */
    color?: ButtonColor;
    /** Disabled state (alias for the native `disabled`). */
    isDisabled?: boolean;
}

export const ButtonGroupItem = ({ iconLeading: IconLeading, iconTrailing: IconTrailing, color = "gray", isDisabled, children, className, type, ...rest }: ButtonGroupItemProps) => {
    const { size } = useContext(ButtonGroupContext);
    const isIcon = (IconLeading || IconTrailing) && !children;

    return (
        <button
            type={type ?? "button"}
            disabled={isDisabled}
            data-icon-only={isIcon ? true : undefined}
            data-icon-leading={IconLeading ? true : undefined}
            className={cx(common, sizes[size], colors[color], className)}
            {...rest}
        >
            {isReactComponent(IconLeading) && <IconLeading data-icon />}
            {isValidElement(IconLeading) && IconLeading}

            {children}

            {isReactComponent(IconTrailing) && <IconTrailing data-icon />}
            {isValidElement(IconTrailing) && IconTrailing}
        </button>
    );
};

export interface ButtonGroupProps extends DetailedHTMLProps<HTMLAttributes<HTMLDivElement>, HTMLDivElement> {
    size?: ButtonSize;
}

export const ButtonGroup = ({ children, size = "md", className, ...rest }: ButtonGroupProps) => {
    return (
        <ButtonGroupContext.Provider value={{ size }}>
            <div role="group" className={cx("relative z-0 inline-flex w-max -space-x-px rounded-lg shadow-xs", className)} {...rest}>
                {children}
            </div>
        </ButtonGroupContext.Provider>
    );
};

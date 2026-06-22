import type { AnchorHTMLAttributes, ButtonHTMLAttributes, DetailedHTMLProps, FC, ReactNode } from "react";
import { isValidElement } from "react";
import type { ButtonProps as AriaButtonProps, LinkProps as AriaLinkProps } from "react-aria-components";
import { Button as AriaButton, Link as AriaLink } from "react-aria-components";
import { cx } from "@/utils/cx";
import { isReactComponent } from "@/utils/is-react-component";

// Untitled UI icon-only "utility" button — the square, label-less control that
// repeats all over Kira's chrome (sidebar collapse/expand, topbar hamburger,
// search-clear, keyboard-shortcuts, notifications bell) and, later, Settings /
// modals / toasts. Vendored from the starter kit and trimmed: the React-Aria
// `Tooltip` overlay is dropped (Kira avoids React-Aria overlays — they caused a
// scroll-jump), so `tooltip` maps to a native `title` + `aria-label`. Styled
// with UUI semantic tokens, which the dark-theme "Kira look bridge" re-points at
// Kira's palette, so it renders on-brand.

const sizes = {
    // 28px — tight slots (inside inputs, dense toolbars).
    xs: "p-1.5 *:data-icon:size-4",
    // 32px — the UUI default.
    sm: "p-1.5 *:data-icon:size-5",
    // 36px — matches Kira's existing chrome icon buttons (size-9).
    md: "p-2 *:data-icon:size-5",
};

const colors = {
    // Bordered chip — pairs with the topbar's bell / shortcuts buttons.
    secondary:
        "bg-secondary text-fg-quaternary shadow-[var(--shadow-1)] ring-1 ring-[var(--border-2)] ring-inset hover:bg-primary_hover hover:text-fg-quaternary_hover",
    // Bare — hover-fill only (sidebar collapse, hamburger, search-clear).
    tertiary: "text-fg-quaternary hover:bg-primary_hover hover:text-fg-quaternary_hover",
};

/** Props shared between the button and anchor variants. */
export interface CommonProps {
    /** Disables the control and dims it to 50%. */
    isDisabled?: boolean;
    /** 28px / 32px / 36px. @default "md" */
    size?: keyof typeof sizes;
    /** Bordered (`secondary`) or bare (`tertiary`). @default "tertiary" */
    color?: keyof typeof colors;
    /** Icon component (preferred) or element. */
    icon?: FC<{ className?: string }> | ReactNode;
    /** Sets the native `title` + `aria-label` (no React-Aria tooltip overlay). */
    tooltip?: string;
}

export interface ButtonProps extends CommonProps, DetailedHTMLProps<Omit<ButtonHTMLAttributes<HTMLButtonElement>, "color" | "slot">, HTMLButtonElement> {
    /** Slot name for react-aria component */
    slot?: AriaButtonProps["slot"];
}

interface LinkProps extends CommonProps, DetailedHTMLProps<Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "color">, HTMLAnchorElement> {
    /** Options for the configured client side router. */
    routerOptions?: AriaLinkProps["routerOptions"];
}

export type Props = ButtonProps | LinkProps;

export const ButtonUtility = ({ tooltip, className, isDisabled, icon: Icon, size = "md", color = "tertiary", ...otherProps }: Props) => {
    const href = "href" in otherProps ? otherProps.href : undefined;
    const Component = href ? AriaLink : AriaButton;

    // `tooltip` is the explicit channel for the accessible name + native hover
    // title; only stamp them when set so a bare `aria-label` on otherProps wins.
    const aria = tooltip ? { "aria-label": tooltip, title: tooltip } : {};

    let props = {};
    if (href) {
        props = {
            ...otherProps,
            href: isDisabled ? undefined : href,
            // Anchors don't support `disabled`; mark it for the `disabled:` selector.
            ...(isDisabled ? { "data-rac": true, "data-disabled": true } : {}),
            ...aria,
        };
    } else {
        props = {
            ...otherProps,
            type: otherProps.type || "button",
            isDisabled,
            ...aria,
        };
    }

    return (
        <Component
            {...props}
            className={cx(
                "group relative inline-flex h-max cursor-pointer items-center justify-center rounded-lg outline-brand transition duration-100 ease-linear pressed:scale-[0.97] focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                sizes[size],
                colors[color],
                // Icon styles
                "*:data-icon:pointer-events-none *:data-icon:shrink-0 *:data-icon:text-current *:data-icon:transition-inherit-all",
                className,
            )}
        >
            {isReactComponent(Icon) && <Icon data-icon />}
            {isValidElement(Icon) && Icon}
        </Component>
    );
};

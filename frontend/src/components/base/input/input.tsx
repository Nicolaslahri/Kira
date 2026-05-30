import type { FC, InputHTMLAttributes, ReactNode } from "react";
import { isValidElement } from "react";
import { cx } from "@/utils/cx";
import { isReactComponent } from "@/utils/is-react-component";

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
    /** Monospace value (for keys, paths, user-agents). */
    mono?: boolean;
    /** Red border for validation errors. */
    invalid?: boolean;
    /** Leading icon component or element. */
    icon?: FC<{ className?: string }> | ReactNode;
    /** Trailing slot — e.g. a show/hide button. */
    trailing?: ReactNode;
    /** Class for the outer wrapper (border/bg/focus ring live here). */
    wrapperClassName?: string;
}

/**
 * Untitled UI text input, re-skinned to Kira's glass tokens. The border + focus
 * ring sit on the wrapper so leading/trailing slots stay inside the field.
 */
export const Input = ({ mono, invalid, icon: Icon, trailing, wrapperClassName, className, ...props }: InputProps) => {
    return (
        <div
            className={cx(
                "flex items-center gap-2 rounded-xl border border-line bg-glass px-3.5 py-2.5 transition-colors focus-within:border-accent-line focus-within:bg-glass-2",
                invalid && "border-conf-low",
                wrapperClassName,
            )}
        >
            {isValidElement(Icon) && Icon}
            {isReactComponent(Icon) && <Icon className="size-4 shrink-0 text-ink-soft" />}
            <input
                className={cx(
                    "min-w-0 flex-1 border-0 bg-transparent text-[13px] text-ink outline-none placeholder:text-ink-faint",
                    mono && "font-mono text-[12.5px]",
                    className,
                )}
                {...props}
            />
            {trailing}
        </div>
    );
};

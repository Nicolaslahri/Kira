import type { Ref } from "react";
import { ChevronDown, ChevronUp } from "@untitledui/icons";
import {
    Button as AriaButton,
    Group as AriaGroup,
    Input as AriaInput,
    NumberField as AriaNumberField,
    type NumberFieldProps as AriaNumberFieldProps,
} from "react-aria-components";
import { cx } from "@/utils/cx";

// Untitled UI number input (React Aria `NumberField`) with the vertical
// stepper column. Vendored from the starter kit, trimmed to drop the
// Label/HintText/Avatar deps Kira doesn't need for inline numeric fields.
// Styled entirely with UUI semantic tokens, which the dark-theme "Kira look
// bridge" (theme.css) re-points at Kira's palette — so it renders on-brand.

const inputSizes = {
    sm: "px-3 py-2 text-sm",
    md: "px-3 py-2 text-md",
    lg: "px-3.5 py-2.5 text-md",
};

export interface InputNumberProps extends AriaNumberFieldProps {
    /** Input size. @default "md" */
    size?: "sm" | "md" | "lg";
    placeholder?: string;
    /** Class for the inner `<input>`. */
    inputClassName?: string;
    /** Class for the bordered group wrapper (use for width sizing). */
    wrapperClassName?: string;
    ref?: Ref<HTMLInputElement>;
}

export const InputNumber = ({ size = "md", placeholder, inputClassName, wrapperClassName, className, ref, ...props }: InputNumberProps) => {
    return (
        <AriaNumberField
            {...props}
            className={(state) => cx("group flex w-full flex-col", typeof className === "function" ? className(state) : className)}
        >
            <AriaGroup
                className={({ isFocusWithin, isDisabled, isInvalid }) =>
                    cx(
                        "relative flex w-full flex-row items-stretch rounded-lg bg-tertiary shadow-xs outline-1 -outline-offset-1 outline-primary transition-all duration-100 ease-linear",
                        isFocusWithin && !isDisabled && "outline-2 -outline-offset-2 outline-brand",
                        isDisabled && "cursor-not-allowed opacity-50",
                        isInvalid && "outline-error_subtle",
                        wrapperClassName,
                    )
                }
            >
                <AriaInput
                    ref={ref}
                    placeholder={placeholder}
                    className={cx(
                        "m-0 w-full bg-transparent text-primary tabular-nums ring-0 outline-hidden placeholder:text-placeholder disabled:cursor-not-allowed",
                        inputSizes[size],
                        inputClassName,
                    )}
                />
                <div className={cx("flex w-7 shrink-0 flex-col border-l border-primary", size === "lg" && "w-7.5")}>
                    <AriaButton
                        slot="increment"
                        className="flex flex-1 cursor-pointer items-center justify-center text-fg-quaternary outline-brand transition duration-100 ease-linear hover:bg-primary_hover hover:text-fg-quaternary_hover disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        <ChevronUp className={cx("size-3 stroke-3", size === "lg" && "size-3.5")} />
                    </AriaButton>
                    <AriaButton
                        slot="decrement"
                        className="flex flex-1 cursor-pointer items-center justify-center border-t border-primary text-fg-quaternary outline-brand transition duration-100 ease-linear hover:bg-primary_hover hover:text-fg-quaternary_hover disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        <ChevronDown className={cx("size-3 stroke-3", size === "lg" && "size-3.5")} />
                    </AriaButton>
                </div>
            </AriaGroup>
        </AriaNumberField>
    );
};

import { motion } from "motion/react";
import { cx } from "@/utils/cx";

export interface ToggleProps {
    /** Whether the switch is on. */
    isSelected: boolean;
    /** Fired when the user flips the switch. */
    onChange: () => void;
    isDisabled?: boolean;
    className?: string;
    "aria-label"?: string;
}

/**
 * iOS-style switch, re-skinned to the emerald accent. The handle animates with
 * a spring. Used anywhere a boolean setting needs a toggle.
 */
export const Toggle = ({ isSelected, onChange, isDisabled, className, ...rest }: ToggleProps) => {
    return (
        <button
            type="button"
            role="switch"
            aria-checked={isSelected}
            aria-label={rest["aria-label"]}
            disabled={isDisabled}
            onClick={onChange}
            className={cx(
                "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                isSelected ? "border-accent-line bg-[var(--accent-soft)]" : "border-line bg-white/[0.06]",
                className,
            )}
        >
            <motion.span
                layout
                transition={{ type: "spring", stiffness: 500, damping: 35 }}
                className={cx("absolute size-[18px] rounded-full", isSelected ? "right-0.5 bg-accent" : "left-0.5 bg-ink-soft")}
            />
        </button>
    );
};

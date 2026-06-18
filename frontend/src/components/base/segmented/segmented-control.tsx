import { cx } from "@/utils/cx";

export interface SegmentedOption {
    value: string;
    label: string;
}

export interface SegmentedControlProps {
    options: SegmentedOption[];
    value: string;
    onChange: (value: string) => void;
    /** Stretch segments to fill the available width in equal parts. */
    fullWidth?: boolean;
    className?: string;
}

/**
 * Untitled UI style segmented control / button group, skinned to Kira's tokens.
 * The selected segment lifts with a lighter fill + shadow.
 */
export const SegmentedControl = ({ options, value, onChange, fullWidth, className }: SegmentedControlProps) => {
    return (
        <div
            role="tablist"
            className={cx(
                "inline-flex gap-0.5 rounded-lg border border-white/[0.1] bg-white/[0.04] p-0.5",
                fullWidth && "flex w-full",
                className,
            )}
        >
            {options.map((o) => {
                const active = o.value === value;
                return (
                    <button
                        key={o.value}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        onClick={() => onChange(o.value)}
                        className={cx(
                            "rounded-md px-3 py-1.5 text-[13px] font-medium outline-none transition-[color,background,box-shadow,transform]",
                            "duration-[var(--dur-2)] ease-[var(--ease-out)]",
                            // Springy toggle feel + visible keyboard focus, matching the
                            // app-wide button system (see "BUTTON SYSTEM" in index.css).
                            "active:scale-[0.96] focus-visible:ring-2 focus-visible:ring-[#6ea8fe] focus-visible:ring-offset-0",
                            fullWidth && "flex-1",
                            active ? "bg-white/[0.1] text-ink shadow-[0_1px_2px_rgba(0,0,0,0.3)]" : "text-ink-muted hover:bg-white/[0.04] hover:text-ink",
                        )}
                    >
                        {o.label}
                    </button>
                );
            })}
        </div>
    );
};

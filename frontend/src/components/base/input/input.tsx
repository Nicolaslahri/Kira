import type { FC, InputHTMLAttributes, ReactNode } from "react";
import { isValidElement, useEffect, useRef, useState } from "react";
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
    /** Gate editing behind an explicit "Edit" button. Until the user clicks
     *  Edit (or the field itself), the input is read-only — so the browser
     *  can't autofill it or pop the "save password?" prompt on a credential /
     *  URL bar that's just sitting in Settings. Click Edit → editable + focused;
     *  blur re-locks. Opt-in: default behavior is unchanged. */
    editGate?: boolean;
    /** When the field is LOCKED (editGate + not editing) and the value is empty,
     *  show this read-only text in place of the blank input — e.g. masked bullets
     *  for a SAVED secret, so it reads as "set" instead of looking empty. Clicking
     *  it (or Edit) unlocks to an empty editable input. Display-only — never the
     *  field's value, so it can't be saved. */
    lockedDisplay?: string;
}

/**
 * Untitled UI text input, re-skinned to Kira's glass tokens. The border + focus
 * ring sit on the wrapper so leading/trailing slots stay inside the field.
 */
export const Input = ({
    mono, invalid, icon: Icon, trailing, wrapperClassName, className,
    editGate, lockedDisplay, readOnly, onBlur, onClick, ...props
}: InputProps) => {
    const [editing, setEditing] = useState(false);
    const ref = useRef<HTMLInputElement>(null);
    // Locked = gated AND not currently editing → read-only, browser leaves it
    // alone. Focus the field once it unlocks so Edit jumps straight to typing.
    const locked = !!editGate && !editing;
    // Show the masked "saved" display only when locked AND nothing's been typed.
    const showLockedDisplay = locked && !!lockedDisplay && !props.value;
    useEffect(() => { if (editing) ref.current?.focus(); }, [editing]);
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
            {showLockedDisplay ? (
                <span
                    onClick={() => setEditing(true)}
                    className={cx(
                        "min-w-0 flex-1 cursor-pointer truncate text-[13px] text-ink select-none",
                        mono && "font-mono text-[12.5px] tracking-[0.12em]",
                    )}
                >
                    {lockedDisplay}
                </span>
            ) : (
                <input
                    ref={ref}
                    readOnly={locked || readOnly}
                    onBlur={(e) => { if (editGate) setEditing(false); onBlur?.(e); }}
                    onClick={(e) => { if (locked) setEditing(true); onClick?.(e); }}
                    className={cx(
                        "min-w-0 flex-1 border-0 bg-transparent text-[13px] text-ink outline-none placeholder:text-ink-faint",
                        mono && "font-mono text-[12.5px]",
                        locked && "cursor-pointer",
                        className,
                    )}
                    {...props}
                />
            )}
            {/* Trailing (eye / browse / clear) stays available even when locked —
                e.g. the path field's Browse must work without clicking Edit.
                Rendered BEFORE the Edit chip: the trailing set varies per field
                (eye, clear, both, neither), so Edit-first landed "Edit" at a
                different offset on every stacked row — Edit-last pins it flush
                right, one aligned column across the whole card. */}
            {trailing}
            {locked && (
                <button
                    type="button"
                    onClick={() => setEditing(true)}
                    className="press shrink-0 rounded-md px-2 py-0.5 text-[11px] font-medium text-ink-soft transition-colors hover:bg-glass-2 hover:text-ink"
                    aria-label="Edit"
                >
                    Edit
                </button>
            )}
        </div>
    );
};

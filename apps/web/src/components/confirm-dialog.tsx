"use client";

import { useEffect, useRef } from "react";
import type { KeyboardEvent } from "react";

interface ConfirmDialogProps {
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Modal confirmation for destructive actions.
 *
 * Uses the native `<dialog>` element opened with `showModal()`, which gives a
 * real focus trap, an inert background, and Escape-to-close for free. Initial
 * focus lands on **Cancel**, so a destructive action is never one Enter away.
 */
export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  cancelLabel,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    // Focus is set explicitly rather than left to the `autofocus` attribute:
    // showModal() moves focus itself, so the two would race. The safe button
    // always wins, which makes the destructive action two keystrokes away.
    cancelRef.current?.focus();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, []);

  // While a request is in flight neither Escape nor Enter may close or re-fire
  // the dialog: the deletion is already committed server-side.
  function handleKeyDown(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      if (!busy) onCancel();
    }
  }

  return (
    <dialog
      ref={dialogRef}
      className="dialog"
      aria-labelledby="confirm-title"
      aria-describedby="confirm-message"
      onKeyDown={handleKeyDown}
    >
      <form
        method="dialog"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy) onConfirm();
        }}
      >
        <h2 id="confirm-title">{title}</h2>
        <p id="confirm-message" className="hint">
          {message}
        </p>
        <div className="row-actions" style={{ marginBlockStart: "1rem" }}>
          <button
            ref={cancelRef}
            className="btn btn-secondary"
            type="button"
            onClick={onCancel}
            disabled={busy}
          >
            {cancelLabel}
          </button>
          <button className="btn btn-danger" type="submit" disabled={busy}>
            {busy ? <span className="spinner" aria-hidden="true" /> : null}
            {confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}

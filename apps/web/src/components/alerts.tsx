"use client";

import type { ReactNode } from "react";

/**
 * Status messages.
 *
 * `role="alert"` makes screen readers announce failures immediately. The message
 * shown is always the server's own safe Arabic message, so what the student sees
 * matches what the API validated.
 */
export function ErrorAlert({ message, onClose }: { message: string; onClose?: () => void }) {
  return (
    <div className="alert alert-error" role="alert">
      <span>{message}</span>
      {onClose ? (
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          onClick={onClose}
          style={{ marginInlineStart: "0.75rem" }}
        >
          ✕
        </button>
      ) : null}
    </div>
  );
}

export function SuccessAlert({ children }: { children: ReactNode }) {
  return (
    <div className="alert alert-success" role="status">
      {children}
    </div>
  );
}

export function InfoAlert({ children }: { children: ReactNode }) {
  return <div className="alert alert-info">{children}</div>;
}

/**
 * Field-level validation errors keyed by the server's `field_errors` map, so the
 * message is attached to the input it refers to.
 */
export function FieldError({ messages }: { messages?: string[] }) {
  if (!messages || messages.length === 0) return null;
  return (
    <p className="field-error" id="field-error">
      {messages.join(" ")}
    </p>
  );
}

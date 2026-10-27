import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "@/components/confirm-dialog";

/**
 * The confirmation dialog guards the only irreversible action in the app, so
 * its behaviour is asserted directly: it must not be dismissible by accident,
 * and it must not double-submit while a request is in flight.
 *
 * Note: the browser (not this code) provides the focus trap and the inert
 * background, because the component uses a native <dialog> with showModal().
 * jsdom does not implement either, so those two properties are deliberately
 * not asserted here.
 */

function setup(props: Partial<Parameters<typeof ConfirmDialog>[0]> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  const result = render(
    <ConfirmDialog
      title="بحث.pdf"
      message="سيُحذف المستند نهائيًا."
      confirmLabel="حذف"
      cancelLabel="إلغاء"
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...props}
    />,
  );
  return { ...result, onConfirm, onCancel };
}

describe("ConfirmDialog", () => {
  it("opens as a modal dialog", async () => {
    setup();
    const dialog = await waitFor(() => screen.getByRole("dialog"));
    expect(dialog.tagName).toBe("DIALOG");
    expect((dialog as HTMLDialogElement).open).toBe(true);
  });

  it("shows the file name and the consequence", () => {
    setup();
    expect(screen.getByText("بحث.pdf")).toBeTruthy();
    expect(screen.getByText("سيُحذف المستند نهائيًا.")).toBeTruthy();
  });

  it("labels itself for assistive technology with real referenced ids", () => {
    setup();
    const dialog = screen.getByRole("dialog");
    const labelId = dialog.getAttribute("aria-labelledby");
    const descId = dialog.getAttribute("aria-describedby");
    expect(labelId).toBeTruthy();
    expect(descId).toBeTruthy();
    expect(document.getElementById(labelId!)?.textContent).toBe("بحث.pdf");
    expect(document.getElementById(descId!)?.textContent).toBe("سيُحذف المستند نهائيًا.");
  });

  it("puts initial focus on Cancel, not on the destructive button", () => {
    setup();
    const cancel = screen.getByRole("button", { name: "إلغاء" });
    const confirm = screen.getByRole("button", { name: "حذف" });
    // React implements autoFocus imperatively, so assert where focus actually
    // landed rather than looking for an attribute.
    expect(document.activeElement).toBe(cancel);
    expect(document.activeElement).not.toBe(confirm);
  });

  it("cancels without confirming", () => {
    const { onConfirm, onCancel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "إلغاء" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("confirms exactly once on submit", () => {
    const { onConfirm, onCancel } = setup();
    fireEvent.click(screen.getByRole("button", { name: "حذف" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("cancels on Escape rather than confirming", () => {
    const { onConfirm, onCancel } = setup();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("blocks both actions while a request is in flight", () => {
    const { onConfirm, onCancel } = setup({ busy: true });
    const confirm = screen.getByRole("button", { name: /حذف/ }) as HTMLButtonElement;
    const cancel = screen.getByRole("button", { name: "إلغاء" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(cancel.disabled).toBe(true);
    fireEvent.click(confirm);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
  });
});

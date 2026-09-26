import { dialogFocusTargetIndex } from "@/lib/watch-contract.mjs";

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

export function focusableDialogElements(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

export function trapDialogTab(dialog: HTMLElement, shiftKey: boolean): boolean {
  const elements = focusableDialogElements(dialog);
  const currentIndex = elements.indexOf(document.activeElement as HTMLElement);
  const targetIndex = dialogFocusTargetIndex(currentIndex, elements.length, shiftKey);
  if (targetIndex < 0) return false;
  elements[targetIndex]?.focus();
  return true;
}

import { useEffect } from "react";

// Keep keyboard focus inside the active modal, restore it on close, and support Escape.
export function useDialogKeyboard() {
  useEffect(() => {
    let current: HTMLElement | null = null;
    let previous: HTMLElement | null = null;
    const focusable = () =>
      current
        ? [
            ...current.querySelectorAll<HTMLElement>(
              "button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]",
            ),
          ].filter((el) => el.offsetParent !== null)
        : [];
    const observer = new MutationObserver(() => {
      const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"]');
      const next = dialogs[dialogs.length - 1] || null;
      if (next === current) return;
      if (next) {
        previous =
          document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;
        current = next;
        focusable()[0]?.focus();
      } else {
        current = null;
        previous?.focus();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    const keydown = (event: KeyboardEvent) => {
      if (!current) return;
      if (event.key === "Escape") {
        current
          .querySelector<HTMLButtonElement>(
            'button[aria-label^="Close"]:not(:disabled)',
          )
          ?.click();
      }
      if (event.key === "Tab") {
        const items = focusable();
        if (!items.length) return;
        const first = items[0],
          last = items[items.length - 1];
        if (
          !current.contains(document.activeElement) ||
          (event.shiftKey && document.activeElement === first)
        ) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      observer.disconnect();
      document.removeEventListener("keydown", keydown);
    };
  }, []);
}

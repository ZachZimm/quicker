import { useEffect } from "react";

// Keep focus inside the active dialog; Escape and backdrop clicks use its close action.
export function useDialogKeyboard() {
  useEffect(() => {
    let current: HTMLElement | null = null;
    let previous: HTMLElement | null = null;
    let pointerStart: EventTarget | null = null;
    const close = () =>
      current
        ?.querySelector<HTMLButtonElement>(
          'button[aria-label^="Close"]:not(:disabled)',
        )
        ?.click();
    const pointerdown = (event: PointerEvent) => {
      pointerStart = event.target;
    };
    const click = (event: MouseEvent) => {
      const target = event.target;
      if (
        target === pointerStart &&
        target instanceof HTMLElement &&
        target === current?.parentElement &&
        target.matches(".modal-overlay, .drawer-overlay")
      )
        close();
      pointerStart = null;
    };
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
        close();
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
    document.addEventListener("pointerdown", pointerdown);
    document.addEventListener("click", click);
    return () => {
      observer.disconnect();
      document.removeEventListener("keydown", keydown);
      document.removeEventListener("pointerdown", pointerdown);
      document.removeEventListener("click", click);
    };
  }, []);
}

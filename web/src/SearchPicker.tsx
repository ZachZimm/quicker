import { useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { createPortal } from "react-dom";

// A real listbox rather than a browser-dependent datalist. The portal keeps the
// list visible outside the register's scrolling container.
export function SearchPicker({
  id,
  label,
  value,
  options,
  error,
  busy,
  onChange,
  onChoose,
  onKeyDown,
  onBlur,
  onCreate,
}: {
  id: string;
  label: string;
  value: string;
  options: string[];
  error?: string;
  busy: boolean;
  onChange: (value: string) => void;
  onChoose: (value: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLInputElement>) => void;
  onBlur: () => void;
  onCreate?: (name: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [filtered, setFiltered] = useState(false);
  const [open, setOpen] = useState(true);
  const [highlight, setHighlight] = useState(-1);
  const [position, setPosition] = useState({
    top: 0,
    left: 0,
    width: 240,
    maxHeight: 240,
  });
  const query = value.trim().toLocaleLowerCase();
  const visible = options.filter(
    (option) => !filtered || option.toLocaleLowerCase().includes(query),
  );
  const canCreate =
    !!onCreate &&
    !!query &&
    !options.some((option) => option.toLocaleLowerCase() === query);
  const count = visible.length + (canCreate ? 1 : 0);
  const choose = (index: number) => {
    if (busy) return;
    if (index === visible.length && canCreate) onCreate!(value.trim());
    else if (visible[index] !== undefined) onChoose(visible[index]);
  };
  useLayoutEffect(() => {
    const place = () => {
      const box = input.current?.getBoundingClientRect();
      if (!box) return;
      const below = innerHeight - box.bottom - 12;
      const above = below < 180 && box.top > below;
      const height = Math.min(240, above ? box.top - 12 : below);
      setPosition({
        top: above ? box.top - height : box.bottom + 3,
        left: Math.max(
          8,
          Math.min(box.left, innerWidth - Math.max(box.width, 240) - 8),
        ),
        width: Math.min(Math.max(box.width, 240), innerWidth - 16),
        maxHeight: Math.max(80, height),
      });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, []);
  useLayoutEffect(() => {
    document
      .getElementById(`${id}-option-${highlight}`)
      ?.scrollIntoView({ block: "nearest" });
  }, [highlight, id]);
  return (
    <>
      <input
        ref={input}
        autoFocus
        role="combobox"
        aria-label={label}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-activedescendant={
          highlight >= 0 ? `${id}-option-${highlight}` : undefined
        }
        aria-invalid={!!error}
        aria-describedby={error ? `${id}-error` : undefined}
        value={value}
        readOnly={busy}
        onFocus={(event) => {
          setOpen(true);
          event.target.select();
        }}
        onChange={(event) => {
          setOpen(true);
          setFiltered(true);
          setHighlight(-1);
          onChange(event.target.value);
        }}
        onBlur={() => {
          setOpen(false);
          onBlur();
        }}
        onKeyDown={(event) => {
          if (busy) return;
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
            setHighlight((old) =>
              count
                ? old < 0
                  ? event.key === "ArrowDown"
                    ? 0
                    : count - 1
                  : (old + (event.key === "ArrowDown" ? 1 : -1) + count) % count
                : -1,
            );
          } else if (event.key === "Enter" && highlight >= 0) {
            event.preventDefault();
            choose(highlight);
          } else if (event.key === "Enter" && canCreate) {
            event.preventDefault();
            onCreate!(value.trim());
          } else onKeyDown(event);
        }}
      />
      {open &&
        createPortal(
          <div
            id={`${id}-list`}
            role="listbox"
            aria-label={`${label} options`}
            className="search-picker-list"
            style={position}
          >
            {visible.map((option, index) => (
              <div
                key={option}
                id={`${id}-option-${index}`}
                role="option"
                aria-selected={highlight === index}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(index)}
              >
                {option}
              </div>
            ))}
            {canCreate && (
              <div
                id={`${id}-option-${visible.length}`}
                role="option"
                aria-selected={highlight === visible.length}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(visible.length)}
              >
                Create “{value.trim()}” in Quicken…
              </div>
            )}
            {!count && <p>No matching options</p>}
          </div>,
          document.body,
        )}
    </>
  );
}

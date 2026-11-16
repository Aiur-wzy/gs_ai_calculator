import type {DisplayRow} from "./display-rows";

export function disclosureLabel(row: DisplayRow, expanded: boolean): string {
  if (row.directory) return expanded ? "收起独立来源" : "展开独立来源";
  return expanded ? "合并实例行" : "展开实例行";
}

interface DisclosurePosition {
  x: number;
  y: number;
  width: number;
  height: number;
  className: string;
  caption?: string;
}

export function createRowDisclosure(document: Document, row: DisplayRow, position: DisclosurePosition, activate: (control: SVGElement) => void) {
  const svg = (tag: string, attributes: Record<string, string | number>) => {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
    return element;
  };
  const {x, y, width, height} = position;
  const description = `${disclosureLabel(row, row.expanded)}：${row.sourceLabel}`;
  const control = svg("g", {class: position.className, role: "button", tabindex: 0, cursor: "pointer",
    "aria-expanded": String(row.expanded), "aria-label": description, "data-axis-control": "row-disclosure",
    "data-axis-row-key": row.key, "data-axis-row-id": row.id});
  const title = svg("title", {});
  title.textContent = description;
  const rect = svg("rect", {x, y, width, height, fill: "transparent", "pointer-events": "all"});
  const text = svg("text", {x: x + 3, y: y + height / 2 + 4, class: "tl-lane-label"});
  text.textContent = `${row.expanded ? "▾" : "▸"}${position.caption ? ` ${position.caption}` : ""}`;
  control.append(title, rect, text);
  control.addEventListener("pointerdown", event => { if (event.button === 0) event.stopPropagation(); });
  control.addEventListener("click", () => activate(control));
  control.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate(control);
    }
  });
  return control;
}

export function restoreRowDisclosureFocus(scope: ParentNode | null | undefined, previous: Element | null): void {
  if (!previous) return;
  const key = previous.getAttribute("data-axis-row-key");
  if (!key) return;
  const id = previous.getAttribute("data-axis-row-id");
  const matches = Array.from(scope?.querySelectorAll<SVGElement>('[data-axis-control="row-disclosure"]') ?? [])
    .filter(control => control.getAttribute("data-axis-row-key") === key);
  (matches.find(control => control.getAttribute("data-axis-row-id") === id) ?? matches[0])?.focus({preventScroll: true});
}

export function updateDisclosureButton(button: HTMLButtonElement, row: DisplayRow, expanded: boolean): void {
  button.textContent = disclosureLabel(row, expanded);
  button.setAttribute("aria-expanded", String(expanded));
}

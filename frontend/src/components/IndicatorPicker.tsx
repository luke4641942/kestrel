import { useState } from "react";
import { api, type IndicatorInfo } from "../api";
import { useResource } from "../hooks";
import { indicatorColor, loadSelection, saveSelection, toggled } from "../indicators";

const NONE: IndicatorInfo[] = [];

/** The indicator menu and this page's selection, remembered in the browser under
 * `storageKey`. `active` holds only keys the menu lists (none until it has loaded), so a
 * stale saved key never reaches the API. */
export function useIndicatorSelection(storageKey: string) {
  const menuResource = useResource(api.indicators, []);
  const menu = menuResource.data ?? NONE;
  const [selected, setSelected] = useState<string[]>(() => loadSelection(storageKey, undefined));
  const active = selected.filter((k) => menu.some((m) => m.key === k));
  const change = (keys: string[]) => {
    setSelected(keys);
    saveSelection(storageKey, keys);
  };
  return { menu, active, change };
}

/** Toggle chips for the indicator menu: drawn over the price, then in panes below. Each
 * chip's tooltip says what the indicator measures. Settings are fixed at textbook values. */
export function IndicatorPicker({
  menu,
  selected,
  onChange,
}: {
  menu: IndicatorInfo[];
  selected: string[];
  onChange: (keys: string[]) => void;
}) {
  const group = (pane: IndicatorInfo["pane"], label: string) => (
    <div className="ind-group" role="group" aria-label={label}>
      <span className="small tertiary">{label}</span>
      {menu
        .filter((m) => m.pane === pane)
        .map((m) => {
          const on = selected.includes(m.key);
          return (
            <button
              key={m.key}
              type="button"
              className={`ind-chip${on ? " on" : ""}`}
              aria-pressed={on}
              title={m.description}
              onClick={() => onChange(toggled(selected, m.key, menu))}
            >
              {on && <i className="swatch" style={{ background: `var(${indicatorColor(menu, m.key)})` }} />}
              {m.label}
            </button>
          );
        })}
    </div>
  );
  return (
    <div className="ind-picker">
      {group("price", "On price")}
      {group("own", "Below")}
    </div>
  );
}

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "../api";
import { type Box, pickRun, placeCard, stepHash, type TourStep } from "../tour";

const WAIT_MS = 4000;

/** Finds a stop's element: by data-tour name, else by id. */
function findTarget(name: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-tour="${CSS.escape(name)}"]`) ?? document.getElementById(name);
}

/** Brings a stop's element into view under the top bar (whose height differs on a phone).
 * A tall element, or any on a phone (where the card covers the bottom), is read from its
 * top; a short one sits in the middle of the space left. */
function scrollToStop(el: HTMLElement, smooth = true) {
  const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const r = el.getBoundingClientRect();
  window.scrollTo({ top: window.scrollY + r.top - wantedTop(el), behavior: smooth && !still ? "smooth" : "auto" });
}

/** Where a stop's element should sit from the top of the screen (see scrollToStop). */
function wantedTop(el: HTMLElement): number {
  const header = document.querySelector(".topbar")?.getBoundingClientRect().height ?? 0;
  const room = window.innerHeight - header;
  const h = el.getBoundingClientRect().height;
  const fromTop = h > room * 0.6 || document.documentElement.clientWidth < 600;
  return fromTop ? header + 12 : header + (room - h) / 2;
}

/** The guided tour: for each stop it opens the page, waits for the element to appear,
 * scrolls to it, rings it, and shows a card with the stop's text. Esc leaves; the arrow
 * keys move. If an element never appears (no saved run, say), the card shows anyway. */
export function Tour({ steps, onClose }: { steps: TourStep[]; onClose: () => void }) {
  const [index, setIndex] = useState(0);
  const [run, setRun] = useState<string | null | undefined>(undefined);
  const [box, setBox] = useState<Box | null>(null);
  const [cardSize, setCardSize] = useState({ width: 360, height: 200 });
  const card = useRef<HTMLDivElement>(null);
  const element = useRef<HTMLElement | null>(null);
  const step = steps[index]!;
  const last = index === steps.length - 1;

  // The run to show, looked up once, only if a stop needs it.
  useEffect(() => {
    if (!steps.some((s) => s.route === "run")) return;
    api
      .backtests()
      .then((runs) => setRun(pickRun(runs)))
      .catch(() => setRun(null));
  }, [steps]);

  const measure = useCallback(() => {
    const el = element.current;
    if (!el || !el.isConnected) return setBox(null);
    const r = el.getBoundingClientRect();
    setBox({ top: r.top, left: r.left, width: r.width, height: r.height });
  }, []);

  // Open the stop's page, then wait for its element.
  useEffect(() => {
    if (step.route === "run" && run === undefined) return; // still choosing the run
    element.current = null;
    setBox(null);
    const hash = stepHash(step, run ?? null);
    if (window.location.hash.replace(/^#/, "") !== hash) window.location.hash = hash;
    const started = Date.now();
    let found = 0;
    // Find the element; then keep the ring on it, and for the first two seconds scroll
    // again if data arriving late moved it (layout shifts fire no scroll event).
    const timer = window.setInterval(() => {
      const now = Date.now();
      if (!found) {
        const el = findTarget(step.target);
        if (el) {
          found = now;
          element.current = el;
          scrollToStop(el);
        } else if (now - started > WAIT_MS) {
          window.clearInterval(timer);
        }
        return;
      }
      const el = element.current;
      if (el && now - found > 600 && now - found < 2500) {
        const atEnd = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
        if (Math.abs(el.getBoundingClientRect().top - wantedTop(el)) > 40 && !atEnd) scrollToStop(el, false);
      }
      measure();
    }, 150);
    return () => window.clearInterval(timer);
  }, [step, run, measure]);

  useEffect(() => {
    window.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    return () => {
      window.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
    };
  }, [measure]);

  useLayoutEffect(() => {
    const el = card.current;
    if (el) setCardSize({ width: el.offsetWidth, height: el.offsetHeight });
  }, [index, box]);

  const next = useCallback(() => (last ? onClose() : setIndex((i) => i + 1)), [last, onClose]);
  const back = useCallback(() => setIndex((i) => Math.max(0, i - 1)), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") next();
      else if (e.key === "ArrowLeft") back();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, back, onClose]);

  useEffect(() => {
    card.current?.focus();
  }, [index]);

  const viewport = { width: document.documentElement.clientWidth, height: window.innerHeight };
  const place = placeCard(box, cardSize, viewport);
  const pad = 6;

  return (
    <div className="tour" aria-live="polite">
      {box ? (
        <div
          className="tour-ring"
          style={{ top: box.top - pad, left: box.left - pad, width: box.width + 2 * pad, height: box.height + 2 * pad }}
        />
      ) : (
        <div className="tour-dim" />
      )}
      <div
        ref={card}
        className={`tour-card${place.sheet ? " tour-sheet" : ""}`}
        style={place.sheet ? undefined : { top: place.top, left: place.left }}
        role="dialog"
        aria-labelledby="tour-title"
        tabIndex={-1}
      >
        <div className="tour-count small tertiary">
          {index + 1} of {steps.length}
        </div>
        <h3 id="tour-title">{step.title}</h3>
        <p className="secondary">{step.body}</p>
        <div className="tour-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Exit
          </button>
          <span className="tour-spacer" />
          {index > 0 && (
            <button type="button" className="btn" onClick={back}>
              Back
            </button>
          )}
          <button type="button" className="btn btn-primary" onClick={next}>
            {last ? "Finish" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}

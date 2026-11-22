import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { pickRun, stepHash, type TourStep, wantedTop } from "../tour";

const WAIT_MS = 4000;
const PAD = 8;

/** Finds a stop's element: by data-tour name, else by id. */
function findTarget(name: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-tour="${CSS.escape(name)}"]`) ?? document.getElementById(name);
}

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/** The guided tour. The card is docked at the bottom and never moves, so Next is always in
 * the same place; the ring does the pointing. For each stop it opens the page, waits for the
 * element, scrolls it into the space above the card, then fades the ring in where it
 * landed. The ring then follows the element every frame (written straight to the DOM, not
 * through React state), so it never trails the page. Esc leaves; the arrow keys move. */
export function Tour({ steps, onClose }: { steps: TourStep[]; onClose: () => void }) {
  const [index, setIndex] = useState(0);
  const [run, setRun] = useState<string | null | undefined>(undefined);
  const [lit, setLit] = useState(false); // the ring is on its element and settled
  const [missing, setMissing] = useState(false); // the element never appeared
  const card = useRef<HTMLDivElement>(null);
  const ring = useRef<HTMLDivElement>(null);
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

  const target = useCallback((el: HTMLElement) => {
    const header = document.querySelector(".topbar")?.getBoundingClientRect().height ?? 0;
    const cardHeight = card.current?.getBoundingClientRect().height ?? 0;
    return wantedTop(el.getBoundingClientRect().height, window.innerHeight, header, cardHeight);
  }, []);

  const scrollTo = useCallback(
    (el: HTMLElement, smooth: boolean) => {
      const top = window.scrollY + el.getBoundingClientRect().top - target(el);
      window.scrollTo({ top, behavior: smooth && !reducedMotion() ? "smooth" : "auto" });
    },
    [target],
  );

  // Open the stop's page, wait for its element, scroll to it, then light the ring.
  useEffect(() => {
    if (step.route === "run" && run === undefined) return; // still choosing the run
    element.current = null;
    setLit(false);
    setMissing(false);
    const hash = stepHash(step, run ?? null);
    if (window.location.hash.replace(/^#/, "") !== hash) window.location.hash = hash;
    const started = Date.now();
    let found = 0;
    let settle: number | undefined;
    const done = () => setLit(true);
    const timer = window.setInterval(() => {
      const now = Date.now();
      if (!found) {
        const el = findTarget(step.target);
        if (el) {
          found = now;
          element.current = el;
          scrollTo(el, true);
          window.addEventListener("scrollend", done, { once: true });
          settle = window.setTimeout(done, reducedMotion() ? 0 : 650); // no scrollend, or no scroll needed
        } else if (now - started > WAIT_MS) {
          window.clearInterval(timer);
          setMissing(true);
        }
        return;
      }
      // For the first few seconds, scroll again if data arriving late moved the element.
      const el = element.current;
      if (!el || now - found > 3000) return window.clearInterval(timer);
      if (now - found < 700) return;
      const atEnd = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
      if (Math.abs(el.getBoundingClientRect().top - target(el)) > 40 && !atEnd) scrollTo(el, false);
    }, 150);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(settle);
      window.removeEventListener("scrollend", done);
    };
  }, [step, run, scrollTo, target]);

  // The ring follows its element every frame, written straight to the DOM.
  useEffect(() => {
    let frame = 0;
    const follow = () => {
      const el = element.current;
      const r = ring.current;
      if (el && r && el.isConnected) {
        const b = el.getBoundingClientRect();
        r.style.transform = `translate(${b.left - PAD}px, ${b.top - PAD}px)`;
        r.style.width = `${b.width + 2 * PAD}px`;
        r.style.height = `${b.height + 2 * PAD}px`;
      }
      frame = window.requestAnimationFrame(follow);
    };
    frame = window.requestAnimationFrame(follow);
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const go = useCallback((i: number) => setIndex(Math.max(0, Math.min(steps.length - 1, i))), [steps.length]);
  const next = useCallback(() => (last ? onClose() : go(index + 1)), [last, onClose, go, index]);
  const back = useCallback(() => go(index - 1), [go, index]);

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
    card.current?.focus({ preventScroll: true });
  }, []);

  // While touring, the page gets room under it the height of the card, so a section near
  // the bottom of a page can still be scrolled clear of the card.
  useEffect(() => {
    const root = document.documentElement;
    const size = () => root.style.setProperty("--tour-card", `${card.current?.offsetHeight ?? 0}px`);
    size();
    root.classList.add("touring");
    window.addEventListener("resize", size);
    return () => {
      window.removeEventListener("resize", size);
      root.classList.remove("touring");
      root.style.removeProperty("--tour-card");
    };
  }, []);

  return (
    <div className="tour">
      <div ref={ring} className={`tour-ring${lit && !missing ? " lit" : ""}`} aria-hidden />
      <div className={`tour-dim${missing || !lit ? " on" : ""}`} aria-hidden />
      <div ref={card} className="tour-card" role="dialog" aria-labelledby="tour-title" aria-describedby="tour-body" tabIndex={-1}>
        <div className="tour-progress" role="group" aria-label="Tour steps">
          {steps.map((s, i) => (
            <button
              key={s.title}
              type="button"
              className={`tour-seg${i < index ? " done" : ""}${i === index ? " now" : ""}`}
              aria-label={`Step ${i + 1}: ${s.title}`}
              aria-current={i === index ? "step" : undefined}
              onClick={() => go(i)}
            />
          ))}
        </div>
        <div className="tour-main">
          <div className="tour-text" key={index} aria-live="polite">
            <div className="tour-chapter">
              {step.chapter} <span className="tour-of">· {index + 1} of {steps.length}</span>
            </div>
            <h3 id="tour-title">{step.title}</h3>
            <p id="tour-body">{step.body}</p>
          </div>
          <div className="tour-side">
            <button type="button" className="tour-close" onClick={onClose} aria-label="Exit the tour" title="Exit (Esc)">
              <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden>
                <path d="M3 3l8 8M11 3l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
            <div className="tour-buttons">
              <span className="tour-keys" aria-hidden>
                <kbd>←</kbd>
                <kbd>→</kbd>
              </span>
              <button type="button" className="btn tour-back" onClick={back} disabled={index === 0}>
                Back
              </button>
              <button type="button" className="btn btn-primary tour-next" onClick={next}>
                {last ? "Finish" : "Next"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

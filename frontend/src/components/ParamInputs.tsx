import type { ParamInfo } from "../api";

/** Whole-number inputs for a strategy's parameters, bounded by their allowed ranges. */
export function ParamInputs({
  params,
  values,
  onChange,
  idPrefix,
}: {
  params: ParamInfo[];
  values: Record<string, number>;
  onChange: (values: Record<string, number>) => void;
  idPrefix: string;
}) {
  if (params.length === 0) return null;
  return (
    <>
      {params.map((p) => (
        <div className="field" key={p.name}>
          <label htmlFor={`${idPrefix}-${p.name}`} title={p.description}>
            {p.name}
          </label>
          <input
            id={`${idPrefix}-${p.name}`}
            type="number"
            min={p.low}
            max={p.high}
            step={1}
            value={values[p.name] ?? p.default}
            onChange={(e) => onChange({ ...values, [p.name]: Math.round(Number(e.target.value)) })}
          />
        </div>
      ))}
    </>
  );
}

export const defaultParams = (params: ParamInfo[]) =>
  Object.fromEntries(params.map((p) => [p.name, p.default])) as Record<string, number>;

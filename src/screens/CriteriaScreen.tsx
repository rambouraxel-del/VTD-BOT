import { availableBrands } from '../services'
import type { SearchCriteria } from '../types'

type Props = { criteria: SearchCriteria; onChange: (c: SearchCriteria) => void; onReset: () => void }

type NumKey = 'maxPrice' | 'minProfit' | 'minRoi' | 'minScore'
const sliders: { key: NumKey; label: string; min: number; max: number; step: number; unit: string }[] = [
  { key: 'maxPrice', label: "Prix d'achat max", min: 10, max: 1000, step: 10, unit: '€' },
  { key: 'minProfit', label: 'Marge minimum', min: 0, max: 300, step: 5, unit: '€' },
  { key: 'minRoi', label: 'ROI minimum', min: 0, max: 200, step: 5, unit: '%' },
  { key: 'minScore', label: 'Score minimum', min: 0, max: 100, step: 5, unit: '/100' },
]

export function CriteriaScreen({ criteria, onChange, onReset }: Props) {
  const set = (patch: Partial<SearchCriteria>) => onChange({ ...criteria, ...patch })
  const toggleBrand = (b: string) =>
    set({ brands: criteria.brands.includes(b) ? criteria.brands.filter((x) => x !== b) : [...criteria.brands, b] })

  return (
    <section className="screen">
      <header className="topbar">
        <h1>Critères</h1>
      </header>

      <div className="form">
        <label className="field">
          <span>Mots-clés</span>
          <input
            type="search"
            placeholder="ex : doudoune, jordan…"
            value={criteria.keywords}
            onChange={(e) => set({ keywords: e.target.value })}
          />
        </label>

        <div className="field">
          <span>Marques {criteria.brands.length === 0 && <em className="muted">(toutes)</em>}</span>
          <div className="chips">
            {availableBrands.map((b) => (
              <button
                key={b}
                className={`chip ${criteria.brands.includes(b) ? 'on' : ''}`}
                onClick={() => toggleBrand(b)}
              >
                {b}
              </button>
            ))}
          </div>
        </div>

        {sliders.map((s) => (
          <label className="field" key={s.key}>
            <span className="field-row">
              {s.label}
              <strong>
                {criteria[s.key]}
                {s.unit}
              </strong>
            </span>
            <input
              type="range"
              min={s.min}
              max={s.max}
              step={s.step}
              value={criteria[s.key]}
              onChange={(e) => set({ [s.key]: Number(e.target.value) })}
            />
          </label>
        ))}

        <p className="muted small-text">Les critères sont enregistrés automatiquement et filtrent le Scanner.</p>

        <button className="btn ghost" onClick={onReset}>
          Réinitialiser les swipes (tests)
        </button>
      </div>
    </section>
  )
}

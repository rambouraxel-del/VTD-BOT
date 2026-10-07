import { ListingImage } from '../components/ListingImage'
import { euro, scoreLevel } from '../components/format'
import type { Listing } from '../types'

type Props = { matches: Listing[]; onRemove: (id: string) => void }

export function MatchesScreen({ matches, onRemove }: Props) {
  const totalProfit = matches.reduce((s, l) => s + l.profit, 0)

  return (
    <section className="screen">
      <header className="topbar">
        <h1>
          Mes Matchs <span className="pill">{matches.length}</span>
        </h1>
        {matches.length > 0 && <span className="total">+{euro(totalProfit)} potentiel</span>}
      </header>

      {matches.length === 0 ? (
        <div className="empty">
          <div className="empty-icon">❤️</div>
          <h2>Aucun match pour l'instant</h2>
          <p>Swipe à droite sur une annonce pour la sauvegarder ici.</p>
        </div>
      ) : (
        <ul className="match-list">
          {matches.map((l) => (
            <li key={l.id} className="match">
              <div className="match-photo">
                <ListingImage src={l.imageUrl} alt={l.title} brand={l.brand} />
              </div>
              <div className="match-info">
                <div className="match-head">
                  <h3>{l.title}</h3>
                  <span className={`score-mini ${scoreLevel(l.score)}`}>{l.score}</span>
                </div>
                <p className="muted">
                  {l.brand} · {l.size} · {l.condition}
                </p>
                <p className="match-prices">
                  {euro(l.price)} → {euro(l.resalePrice)}{' '}
                  <strong className="profit-text">
                    +{euro(l.profit)} ({l.roi}%)
                  </strong>
                </p>
                <div className="match-actions">
                  <a className="btn primary small" href={l.vintedUrl} target="_blank" rel="noreferrer">
                    Voir sur Vinted
                  </a>
                  <button className="btn ghost small" onClick={() => onRemove(l.id)} aria-label="Retirer">
                    Retirer
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

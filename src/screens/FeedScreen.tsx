import { useRef } from 'react'
import { SwipeDeck, type SwipeDeckHandle } from '../components/SwipeDeck'
import type { Listing } from '../types'

type Props = {
  feed: Listing[]
  canUndo: boolean
  onSwipe: (l: Listing, dir: 'left' | 'right') => void
  onUndo: () => void
  onReset: () => void
  onOpenCriteria: () => void
}

export function FeedScreen({ feed, canUndo, onSwipe, onUndo, onReset, onOpenCriteria }: Props) {
  const deck = useRef<SwipeDeckHandle>(null)

  return (
    <section className="screen feed">
      <header className="topbar">
        <h1>
          Scanner <span className="pill">{feed.length}</span>
        </h1>
        <button className="icon-btn" onClick={onOpenCriteria} aria-label="Critères">
          ⚙️
        </button>
      </header>

      {feed.length > 0 ? (
        <>
          <SwipeDeck ref={deck} listings={feed} onSwipe={onSwipe} />
          <div className="actions">
            <button className="action nope" onClick={() => deck.current?.swipe('left')} aria-label="Ignorer">
              ❌
            </button>
            <button className="action undo" onClick={onUndo} disabled={!canUndo} aria-label="Annuler">
              ↺
            </button>
            <button className="action like" onClick={() => deck.current?.swipe('right')} aria-label="Matcher">
              ❤️
            </button>
          </div>
        </>
      ) : (
        <div className="empty">
          <div className="empty-icon">🔍</div>
          <h2>Plus d'annonces pour le moment</h2>
          <p>Élargis tes critères ou reviens plus tard.</p>
          <div className="empty-actions">
            {canUndo && (
              <button className="btn ghost" onClick={onUndo}>
                ↺ Annuler le dernier swipe
              </button>
            )}
            <button className="btn ghost" onClick={onOpenCriteria}>
              Modifier les critères
            </button>
            <button className="btn ghost" onClick={onReset}>
              Recharger les annonces de test
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

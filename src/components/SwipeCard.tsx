import type { Listing } from '../types'
import { euro, scoreLevel } from './format'
import { ListingImage } from './ListingImage'

/** Contenu visuel d'une carte (sans logique de swipe). */
export function SwipeCard({ listing }: { listing: Listing }) {
  return (
    <div className="card">
      <div className="card-photo">
        <ListingImage src={listing.imageUrl} alt={listing.title} brand={listing.brand} />
        <div className={`score-badge ${scoreLevel(listing.score)}`}>
          <strong>{listing.score}</strong>
          <small>/100</small>
        </div>
        <div className="stamp like">MATCH</div>
        <div className="stamp nope">PASSER</div>
        {listing.tags && listing.tags.length > 0 && (
          <div className="card-tags">
            {listing.tags.slice(0, 3).map((t) => (
              <span key={t}>{t}</span>
            ))}
          </div>
        )}
      </div>
      <div className="card-body">
        <div className="card-title">
          <h2>{listing.title}</h2>
          <p>
            {listing.brand} · Taille {listing.size} · {listing.condition}
          </p>
        </div>
        <div className="card-stats">
          <div>
            <small>Achat</small>
            <strong>{euro(listing.price)}</strong>
          </div>
          <div>
            <small>Revente</small>
            <strong>{euro(listing.resalePrice)}</strong>
          </div>
          <div className="profit">
            <small>Marge</small>
            <strong>+{euro(listing.profit)}</strong>
          </div>
          <div className="profit">
            <small>ROI</small>
            <strong>{listing.roi}%</strong>
          </div>
        </div>
      </div>
    </div>
  )
}

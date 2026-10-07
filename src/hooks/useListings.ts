import { useCallback, useEffect, useState } from 'react'
import { defaultCriteria, listingService } from '../services'
import type { Listing, SearchCriteria } from '../types'

/** État global de l'app. Passe uniquement par listingService (mock ou API). */
export function useListings() {
  const [feed, setFeed] = useState<Listing[]>([])
  const [matches, setMatches] = useState<Listing[]>([])
  const [criteria, setCriteria] = useState<SearchCriteria>(defaultCriteria)
  const [brands, setBrands] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastSwiped, setLastSwiped] = useState<Listing | null>(null)

  const refresh = useCallback(async (c: SearchCriteria) => {
    const [f, m] = await Promise.all([listingService.getFeed(c), listingService.getMatches()])
    setFeed(f)
    setMatches(m)
  }, [])

  useEffect(() => {
    ;(async () => {
      try {
        const [c, b] = await Promise.all([listingService.getCriteria(), listingService.getBrands()])
        setCriteria(c)
        setBrands(b)
        await refresh(c)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setLoading(false)
      }
    })()
  }, [refresh])

  const swipe = useCallback(async (listing: Listing, dir: 'left' | 'right') => {
    // Mise à jour optimiste : l'UI réagit immédiatement.
    setFeed((f) => f.filter((l) => l.id !== listing.id))
    if (dir === 'right') setMatches((m) => [{ ...listing, status: 'matched' }, ...m])
    setLastSwiped(listing)
    await listingService.setStatus(listing.id, dir === 'right' ? 'matched' : 'ignored')
  }, [])

  const undo = useCallback(async () => {
    if (!lastSwiped) return
    await listingService.setStatus(lastSwiped.id, 'new')
    setFeed((f) => [{ ...lastSwiped, status: 'new' }, ...f])
    setMatches((m) => m.filter((l) => l.id !== lastSwiped.id))
    setLastSwiped(null)
  }, [lastSwiped])

  const removeMatch = useCallback(async (id: string) => {
    setMatches((m) => m.filter((l) => l.id !== id))
    await listingService.setStatus(id, 'ignored')
  }, [])

  const updateCriteria = useCallback(
    async (c: SearchCriteria) => {
      setCriteria(c)
      await listingService.saveCriteria(c)
      await refresh(c)
    },
    [refresh],
  )

  const resetAll = useCallback(async () => {
    await listingService.reset()
    setLastSwiped(null)
    await refresh(criteria)
  }, [criteria, refresh])

  return { feed, matches, criteria, brands, loading, error, canUndo: !!lastSwiped, swipe, undo, removeMatch, updateCriteria, resetAll }
}

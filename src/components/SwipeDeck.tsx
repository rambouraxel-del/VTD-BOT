import { forwardRef, useImperativeHandle, useRef } from 'react'
import type { Listing } from '../types'
import { SwipeCard } from './SwipeCard'

export type SwipeDir = 'left' | 'right'
export type SwipeDeckHandle = { swipe: (dir: SwipeDir) => void }

type Props = { listings: Listing[]; onSwipe: (listing: Listing, dir: SwipeDir) => void }

const THRESHOLD = 110 // px à dépasser pour valider un swipe
const VELOCITY = 0.6 // px/ms : un geste rapide valide aussi

/**
 * Pile de cartes swipables. Le drag écrit directement dans le style
 * (sans re-render React) pour rester fluide à 60 fps sur iPhone.
 */
export const SwipeDeck = forwardRef<SwipeDeckHandle, Props>(function SwipeDeck({ listings, onSwipe }, ref) {
  const topRef = useRef<HTMLDivElement>(null)
  const drag = useRef({ active: false, startX: 0, startY: 0, x: 0, y: 0, t: 0, lastX: 0, v: 0 })
  const exiting = useRef(false)

  const top = listings[0]
  const next = listings[1]

  const apply = (x: number, y: number, animate: boolean) => {
    const el = topRef.current
    if (!el) return
    el.style.transition = animate ? 'transform 0.35s cubic-bezier(.2,.8,.3,1)' : 'none'
    el.style.transform = `translate3d(${x}px, ${y}px, 0) rotate(${x / 18}deg)`
    const p = Math.max(-1, Math.min(1, x / THRESHOLD))
    el.style.setProperty('--like', String(Math.max(0, p)))
    el.style.setProperty('--nope', String(Math.max(0, -p)))
  }

  const fling = (dir: SwipeDir) => {
    if (!top || exiting.current) return
    exiting.current = true
    const w = window.innerWidth
    apply(dir === 'right' ? w * 1.4 : -w * 1.4, drag.current.y + 40, true)
    if (navigator.vibrate) navigator.vibrate(10)
    window.setTimeout(() => {
      exiting.current = false
      drag.current.x = drag.current.y = 0
      onSwipe(top, dir)
    }, 260)
  }

  useImperativeHandle(ref, () => ({ swipe: fling }))

  const onPointerDown = (e: React.PointerEvent) => {
    if (exiting.current) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { active: true, startX: e.clientX, startY: e.clientY, x: 0, y: 0, t: e.timeStamp, lastX: e.clientX, v: 0 }
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d.active) return
    d.x = e.clientX - d.startX
    d.y = (e.clientY - d.startY) * 0.4
    const dt = e.timeStamp - d.t
    if (dt > 0) d.v = (e.clientX - d.lastX) / dt
    d.t = e.timeStamp
    d.lastX = e.clientX
    apply(d.x, d.y, false)
  }

  const onPointerUp = () => {
    const d = drag.current
    if (!d.active) return
    d.active = false
    if (d.x > THRESHOLD || (d.v > VELOCITY && d.x > 30)) fling('right')
    else if (d.x < -THRESHOLD || (d.v < -VELOCITY && d.x < -30)) fling('left')
    else apply(0, 0, true)
  }

  return (
    <div className="deck">
      {next && (
        <div className="deck-card behind" key={next.id}>
          <SwipeCard listing={next} />
        </div>
      )}
      {top && (
        <div
          className="deck-card top"
          key={top.id}
          ref={topRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <SwipeCard listing={top} />
        </div>
      )}
    </div>
  )
})

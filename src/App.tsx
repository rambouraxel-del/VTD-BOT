import { useState } from 'react'
import { TabBar, type Tab } from './components/TabBar'
import { useListings } from './hooks/useListings'
import { CriteriaScreen } from './screens/CriteriaScreen'
import { FeedScreen } from './screens/FeedScreen'
import { MatchesScreen } from './screens/MatchesScreen'

export default function App() {
  const [tab, setTab] = useState<Tab>('feed')
  const s = useListings()

  return (
    <div className="app">
      <main>
        {s.loading ? (
          <div className="empty">Chargement…</div>
        ) : tab === 'feed' ? (
          <FeedScreen
            feed={s.feed}
            canUndo={s.canUndo}
            onSwipe={s.swipe}
            onUndo={s.undo}
            onReset={s.resetAll}
            onOpenCriteria={() => setTab('criteria')}
          />
        ) : tab === 'matches' ? (
          <MatchesScreen matches={s.matches} onRemove={s.removeMatch} />
        ) : (
          <CriteriaScreen criteria={s.criteria} onChange={s.updateCriteria} onReset={s.resetAll} />
        )}
      </main>
      <TabBar active={tab} onChange={setTab} badge={s.matches.length} />
    </div>
  )
}

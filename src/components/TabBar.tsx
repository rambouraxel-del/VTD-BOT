export type Tab = 'feed' | 'matches' | 'criteria'

const tabs: { id: Tab; icon: string; label: string }[] = [
  { id: 'feed', icon: '🔥', label: 'Scanner' },
  { id: 'matches', icon: '❤️', label: 'Matchs' },
  { id: 'criteria', icon: '🎯', label: 'Critères' },
]

export function TabBar({ active, onChange, badge }: { active: Tab; onChange: (t: Tab) => void; badge: number }) {
  return (
    <nav className="tabbar">
      {tabs.map((t) => (
        <button key={t.id} className={active === t.id ? 'active' : ''} onClick={() => onChange(t.id)}>
          <span className="tab-icon">
            {t.icon}
            {t.id === 'matches' && badge > 0 && <i className="badge">{badge}</i>}
          </span>
          <span>{t.label}</span>
        </button>
      ))}
    </nav>
  )
}

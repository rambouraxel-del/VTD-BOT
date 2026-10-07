import { useState } from 'react'
import { apiKeyStore } from '../services'

/** Affiché uniquement en mode API quand le serveur refuse la clé (401). */
export function ApiKeyScreen() {
  const [key, setKey] = useState('')

  const save = (e: React.FormEvent) => {
    e.preventDefault()
    apiKeyStore.set(key)
    window.location.reload()
  }

  return (
    <form className="empty" onSubmit={save}>
      <div className="empty-icon">🔑</div>
      <h2>Clé d'accès requise</h2>
      <p>Colle la clé API de ton serveur (variable API_KEY). Elle reste enregistrée sur cet appareil.</p>
      <div className="empty-actions">
        <input
          className="key-input"
          type="password"
          autoComplete="off"
          placeholder="Clé API"
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <button className="btn primary" type="submit" disabled={key.trim().length < 32}>
          Se connecter
        </button>
      </div>
    </form>
  )
}

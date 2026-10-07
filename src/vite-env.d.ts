/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "mock" (défaut) ou "api" */
  readonly VITE_DATA_SOURCE?: 'mock' | 'api'
  /** URL du pont API. Vide = même origine (proxy Vite en dev). */
  readonly VITE_API_URL?: string
}

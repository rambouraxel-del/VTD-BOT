export const euro = (n: number) =>
  n.toLocaleString('fr-FR', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 })

export const scoreLevel = (score: number) => (score >= 80 ? 'high' : score >= 65 ? 'mid' : 'low')

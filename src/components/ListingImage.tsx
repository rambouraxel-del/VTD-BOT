import { useState } from 'react'

/** Image avec repli élégant si la photo ne charge pas. */
export function ListingImage({ src, alt, brand }: { src: string; alt: string; brand: string }) {
  const [failed, setFailed] = useState(false)
  if (failed || !src) {
    return (
      <div className="img-fallback">
        <span>{brand.charAt(0)}</span>
      </div>
    )
  }
  return <img src={src} alt={alt} draggable={false} onError={() => setFailed(true)} />
}

import { useEffect } from "react"

// Set document.title to "<page> · ViralMint" for the lifetime of the
// component. Restores the previous title on unmount so SPA route changes
// don't accumulate stale suffixes. Multi-tab users care: tab-bar text is
// the primary way they distinguish Library vs Chat vs Settings.
//
// Pass null/undefined/"" to skip — the page will keep whatever title
// was set previously (e.g. by the lazy-loading parent).
export default function useDocumentTitle(name) {
  useEffect(() => {
    if (!name) return
    const prev = document.title
    document.title = `${name} · ViralMint`
    return () => {
      document.title = prev
    }
  }, [name])
}

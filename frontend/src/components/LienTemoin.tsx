/**
 * Le lien vers le témoin Ethernet, dans l'en-tête.
 *
 * L'exécutable unique lance le superviseur et le témoin dans le même
 * processus ; `/api/health` publie alors l'adresse du témoin (au poste local
 * seulement : le témoin n'écoute que sur 127.0.0.1). Lancé seul, le
 * superviseur ne publie rien, et le lien n'existe pas.
 */
export function LienTemoin({ adresse }: { adresse: string | null | undefined }) {
  if (!adresse) return null
  return <a className="tag" href={adresse}>Témoin Ethernet</a>
}

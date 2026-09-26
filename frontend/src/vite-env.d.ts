/* Les types que Vite ajoute à `import.meta` — `glob`, `env`, `hot`.
   Le projet s'en était passé jusqu'ici : rien n'y touchait `import.meta`. Le
   garde du pied de notes lit les sources par `import.meta.glob`, qui passe par
   le resolver de Vite là où `node:fs` demanderait des types Node que ce paquet
   frontal n'a pas. */
/// <reference types="vite/client" />

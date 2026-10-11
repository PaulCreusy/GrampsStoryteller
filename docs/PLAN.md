# Plan de développement — GrampsStoryteller

Plugin Gramps qui rédige, pour chaque personne sélectionnée, une **mise en
contexte historique** du lieu et de l'époque où elle a vécu, à partir de sources
Wikipédia, sans jamais transmettre d'informations identifiantes à un service
externe. Le texte est stocké dans une note attachée à la personne.

## 1. Principes

### 1.1 Confidentialité

- **Personnes vivantes exclues systématiquement** (`probably_alive`), sans option
  pour les inclure. Personnes et événements marqués privés exclus.
- Seules données envoyées (à Wikipédia et au LLM) :
  - **année** + lieu de naissance (repli : baptême) ;
  - **année** + lieu de décès (repli : inhumation) ;
  - profession, telle quelle, **uniquement si l'option est cochée** (désactivée
    par défaut).
- Les repères sont envoyés **sans libellé** (ni « naissance » ni « décès »),
  triés chronologiquement. Exemple :
  `Repères : vers 1823 – Plouguerneau (Finistère, Bretagne, France) ; 1891 – Brest (Finistère, Bretagne, France).`
- Jamais envoyés : nom, ID, conjoints, mariage, enfants, notes, sources de
  l'arbre, jour et mois des dates.
- Une personne sans aucun repère exploitable est ignorée et signalée.
- Bouton **« Aperçu de la requête »** : affiche exactement ce qui sera envoyé.

### 1.2 Fidélité (« ne rien inventer »)

- Le LLM rédige à partir des **extraits Wikipédia fournis** et cite chaque
  affirmation (`[1]`, `[2]`…).
- Il **peut compléter avec des connaissances générales**, mais doit les
  signaler explicitement (balise dédiée rendue en italique avec la mention
  « connaissance générale, non sourcée »).
- Si les sources sont riches (score calculé localement, cf. §4.2), le prompt
  lui demande de **ne pas** recourir aux connaissances générales.
- Aucune supposition sur une personne : le texte décrit un contexte, pas une
  vie vécue.

### 1.3 Note produite

- Contenu : **contexte + liste des sources** uniquement (pas d'en-tête).
- Type de note personnalisé « Récit de vie » et tag `Storyteller`, rattachée à
  la personne.
- Régénération : la note portant le tag `Storyteller` est **remplacée**.
- Écriture dans un `DbTxn` par personne (annulable).

## 2. Architecture

| Sujet | Choix |
|---|---|
| Cible | Gramps 6.0, puis 5.2 et 5.1 (phase finale) |
| Plugin | `register(TOOL, …)`, `category=TOOL_DBPROC`, `tool_modes=[TOOL_MODE_GUI]` |
| HTTP | `urllib.request` (stdlib) — aucune dépendance à installer |
| Fournisseurs LLM | Compatible OpenAI par défaut (OpenAI, Mistral, Groq, OpenRouter, Ollama, LM Studio), Anthropic, Gemini |
| Sources | API MediaWiki de Wikipédia, dans la langue du récit |
| Cache | Disque, extraits par page et contextes générés par (lieux, périodes) |
| Validation LLM | **Optionnelle, désactivée par défaut** ; contrôles locaux toujours actifs |
| Installation | Gestionnaire d'extensions Gramps, via un projet tiers pointant sur ce dépôt (méthode GeneanetForGramps) |

```
GrampsStoryteller/
├── GrampsStoryteller.gpr.py
├── GrampsStoryteller.py
├── src/
│   ├── gui/{tool_window.py, settings_dialog.py, preview_dialog.py}
│   ├── extraction.py      # Birth/death (with fallbacks) year+place, occupation; living/private filters
│   ├── anonymizer.py      # Facts -> unlabeled, sorted landmarks
│   ├── selection.py       # Active person, selection, ancestors/descendants, custom filter
│   ├── sources/
│   │   ├── wikipedia.py   # MediaWiki API client
│   │   ├── hierarchy.py   # Commune -> department -> region -> country fallback chain
│   │   ├── sections.py    # "History" section extraction and period filtering
│   │   └── cache.py
│   ├── llm/{base.py, openai_compat.py, anthropic.py, gemini.py, registry.py}
│   ├── prompts/{default_fr.txt, default_en.txt, validation_fr.txt, builder.py}
│   ├── validation.py      # Local checks + optional LLM check
│   ├── notes.py           # Note assembly and replacement
│   └── config.py          # Settings + API key (keyring or 600 file)
├── po/
├── scripts/build_addon.py
├── addons/gramps60/{download,listings}/
├── tests/
└── .github/workflows/ci.yml
```

## 3. Phases

### Phase 0 — Socle installable

- `.gpr.py` + outil minimal ouvrant une fenêtre.
- `scripts/build_addon.py` adapté de GeneanetForGramps (inclut `src/**/*.py`
  et `src/prompts/*.txt`).
- `addons/gramps60/` publié sur `main`. URL de projet :
  `https://raw.githubusercontent.com/PaulCreusy/GrampsStoryteller/main/addons/gramps60`
- CI : tests + vérification que l'archive et les listings sont à jour.

**Livrable** : installation depuis *Aide ▸ Gestionnaire d'extensions ▸ Projets ▸ +*.

### Phase 1 — Extraction minimale et anonymisation

- Naissance (repli baptême), décès (repli inhumation).
- Année seule, avec qualificatif (« vers », « avant », « après », « entre »).
- Hiérarchie de lieu complète depuis Gramps (commune, département, région,
  pays) + coordonnées si présentes (servent uniquement à la recherche).
- Profession (attribut ou événement) si l'option est active.
- Filtres vivants / privés.

**Livrable** : aperçu de la requête anonymisée + tests unitaires.

### Phase 2 — Contexte sourcé avec repli hiérarchique

Voir §4.

### Phase 3 — Couche LLM et configuration

- Fournisseurs avec timeout, retries (backoff sur 429/5xx), erreurs lisibles.
- Fenêtre **Paramètres** :
  - fournisseur, modèle, URL de base, clé d'API masquée (keyring sinon
    `~/.config/grampsstoryteller/credentials.ini` en 600 ; variable
    d'environnement prioritaire), bouton **Tester la connexion** ;
  - prompt éditable + **Réinitialiser** ; variables `{reperes}`,
    `{profession}`, `{sources}`, `{consigne_sources}`, `{langue}`,
    `{longueur}` ;
  - langue, longueur, ton ;
  - option « Fournir la profession » (désactivée par défaut) ;
  - option « Validation par LLM » (désactivée par défaut) + prompt éditable.

### Phase 4 — Génération, validation, stockage

- Contrôles locaux (gratuits, toujours actifs) :
  - chaque `[n]` correspond à une source fournie, aucune URL inventée ;
  - texte non vide et non tronqué ;
  - aucune donnée exclue ne réapparaît ;
  - comptage des passages marqués « connaissance générale » (signalé si la
    source était jugée riche).
- Validation LLM optionnelle : seconde passe vérifiant l'appui de chaque phrase
  sur un extrait ou son marquage comme connaissance générale.
- Écriture / remplacement de la note (cf. §1.3).

### Phase 5 — Traitement par lot

- Sélection : personne active, sélection de la vue Personnes,
  ascendants/descendants sur N générations, filtre personnalisé Gramps, tout
  l'arbre.
- Regroupement des personnes aux repères identiques → un seul appel par groupe
  (cache).
- Estimation (nombre d'appels Wikipédia/LLM, tokens) et confirmation avant
  lancement.
- Thread de travail, barre de progression, bouton **Arrêter**
  (`GLib.idle_add` pour l'UI, écritures en base dans le thread principal).
- Rapport : notes créées/remplacées, personnes ignorées (vivantes, sans
  repère), erreurs, alertes de validation.

### Phase 6 — Finitions

- Traduction FR (`po/`), README (installation, configuration, confidentialité,
  limites), icône.
- Procédure de release : version dans le `.gpr.py` → build → commit.

### Phase 7 — Support Gramps 5.2 et 5.1

- `build_addon.py --gramps-version {5.1,5.2,6.0}` → `addons/gramps51/`,
  `gramps52/`, `gramps60/`.
- Uniquement l'API objet (`get_person_from_handle`…), jamais les données
  brutes (tuples en 5.x, JSON en 6.0).
- Audit des écarts d'API : filtres, `probably_alive`, `NoteType` personnalisé,
  gestionnaire de config.
- Matrice CI sur les trois versions ; README avec les trois URL de projet.

## 4. Contexte sourcé — détail

### 4.1 Chaîne de repli par lieu

Pour chaque lieu, on collecte des extraits à plusieurs niveaux, du plus local
au plus large :

1. **Commune** — article de la commune, section « Histoire ».
2. **Département** — article du département, section « Histoire ».
3. **Région** — article de la région, section « Histoire ». Pour les dates
   anciennes, on privilégie l'**ancienne province / région historique**
   (ex. Bretagne, Normandie) plutôt que la région administrative actuelle.
4. **Pays** — article de la période nationale (ex. « Restauration (histoire de
   France) », « Seconde République »), choisi par une table année → période,
   puis par recherche en repli.

Résolution de chaque niveau :

- d'abord la **hiérarchie de lieux Gramps** (`PlaceRef` parents) ;
- si un niveau manque, recherche Wikipédia par titre
  (« <commune> (<département>) » puis « <commune> »), et en dernier recours
  `geosearch` sur les coordonnées ;
- les homonymes sont départagés par le département/pays connu.

### 4.2 Sélection et richesse

- Découpage des sections « Histoire » en paragraphes ; on garde ceux qui
  mentionnent une année ou un siècle compatible avec la plage
  `[année min − marge, année max + marge]`.
- Remplissage d'un **budget de tokens** du niveau le plus local au plus large.
- Score de richesse = volume d'extraits pertinents aux niveaux commune +
  département. Au-dessus d'un seuil, `{consigne_sources}` interdit les
  connaissances générales ; en dessous, elle les autorise avec marquage.
- Chaque extrait est étiqueté par son niveau dans le prompt
  (`[2] Finistère (département) — …`) pour que le LLM n'attribue pas à la
  commune un fait départemental ou régional.

### 4.3 Cache et sources dans la note

- Cache disque des extraits (URL, révision, date de récupération).
- Section « Sources » de la note : titre de l'article, niveau, URL de la
  révision utilisée.

## 5. Tests

- Sans Gramps : anonymisation, chaîne de repli (HTTP simulé), filtrage par
  période, construction du prompt, fournisseurs LLM (HTTP simulé), contrôles
  locaux.
- Avec Gramps : extraction sur `example.gramps` dans la CI.
- Manuel : installation via le gestionnaire d'extensions sur un Gramps propre.

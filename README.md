# ChatRaoké 🐱🎤

Un karaoké qui s'imprime : on choisit une chanson, elle démarre dans Spotify et les paroles
sortent en rythme d'une mini-imprimante thermique Bluetooth (« cat printer », testé avec une
SC03h). Au lancement, un rideau de théâtre s'ouvre (à l'écran et sur papier).

## Ce qu'il faut

- un Mac (Spotify est piloté par AppleScript, le Bluetooth via CoreBluetooth)
- l'app **Spotify** installée
- Python 3.12 ou plus récent
- une imprimante thermique Bluetooth du type « cat printer » (service BLE `ae30`) — facultatif :
  sans imprimante, l'interface marche quand même (décocher « Imprimer les paroles »)

## Installation

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Lancer

```sh
.venv/bin/python serveur.py            # ouvre http://localhost:8765
.venv/bin/python serveur.py --debug    # sans le rideau d'intro
```

La première fois, macOS demande d'autoriser le terminal à utiliser le Bluetooth et à contrôler
Spotify : accepter. Le nom de l'imprimante recherchée est `CIBLE_DEFAUT` dans `paroles.py`.

## Ajouter des chansons

Les paroles ne sont **pas** dans le dépôt (droits d'auteur) : chacun ajoute ses propres fichiers
`.lrc` (paroles synchronisées, faciles à trouver en ligne) dans le dossier du projet ou dans `chansons/`.

En plus des balises LRC habituelles, deux balises maison :

```
[ti: Titre]
[ar: Artiste]
[length: 03:12]
[spotify: https://open.spotify.com/track/…]   ← lien du morceau : un clic le lance dans Spotify
[jeu: Début de la ligne | suite à deviner]    ← (facultatif) la ligne mystère du jeu
[00:12.34] première ligne
[00:15.80] deuxième ligne
```

## Le jeu « Le chat a mangé les paroles »

Onglet 🐱 : la musique se coupe juste avant une ligne, qui s'affiche avec des trous
(`Il pleut __ ____ __ _____`). On tape la suite (100 points sans faute, 10 par mot juste) ou on
prend le joker 🃏 (4 propositions, 50 points). Accents, majuscules, ponctuation et petites fautes
sont tolérés. La ligne vient de la balise `[jeu: …]`, sinon elle est tirée au hasard.

## Fichiers

| Fichier | Rôle |
|---|---|
| `serveur.py` | serveur local : interface, Spotify, impression |
| `interface.html` | l'interface web |
| `jeu.py` | logique du jeu (trous, correction, joker) |
| `paroles.py` | lecture des `.lrc`, rendu du texte, suivi de Spotify (+ version ligne de commande) |
| `rideau.py` | dessin du rideau imprimé (`python rideau.py apercu.png` pour le voir) |
| `coeur.py` | protocole de l'imprimante (cat printer BLE / ESC/POS) et premier test : des coeurs |

Réglages en haut de `serveur.py` : `SORTIE` (distance tête → sortie du papier), `ECART` (blanc
entre les lignes), `AVANCE_TEMPS`, `INTRO_URI` (musique du rideau), `INTRO_IMPRESSION`.

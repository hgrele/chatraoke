"""Le chat a mangé les paroles : logique du jeu, sans imprimante ni Spotify.

Une manche : la musique se coupe juste avant une ligne ; le joueur voit
« il pleut __ ____ __ _____ » et doit retrouver les mots manquants.
"""

import difflib
import random
import re
import unicodedata

POINTS_PARFAIT = 100   # réponse tapée sans faute
POINTS_PAR_MOT = 10    # réponse tapée partiellement juste
POINTS_JOKER = 50      # bonne réponse avec le joker (choix multiple)
LIGNES_AVANT = 4       # nombre de lignes jouées avant la ligne mystère
NB_CHOIX = 4


def mots(texte: str) -> list[str]:
    return texte.split()


# « d'javel » et « de javel » comptent pareil : on remplace les élisions par le mot entier
ELISIONS = {"d": "de", "j": "je", "l": "le", "la": "le", "m": "me", "t": "te", "s": "se",
            "n": "ne", "c": "ce", "qu": "que"}


def normaliser(texte: str) -> list[str]:
    """Minuscules, sans accents ni ponctuation ; « j'ai » → ["je", "ai"]."""
    texte = unicodedata.normalize("NFD", texte.lower())
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    texte = re.sub(r"[’'`-]", " ", texte)
    texte = re.sub(r"[^\w\s]", "", texte)
    return [ELISIONS.get(m, m) for m in texte.split()]


def cible_balisee(lignes: list[tuple[float, str]], balises: list[str], hasard=random):
    """Balises du .lrc, ex. « [jeu: Il pleut | de l'eau d'javel] » : la ligne à faire trouver,
    et l'endroit où la couper (le « | »). Renvoie (index, visible, caches) ou None."""
    candidats = []
    for b in balises:
        cle = normaliser(b.replace("|", " "))
        for i, (_, t) in enumerate(lignes):
            if cle and normaliser(t)[:len(cle)] == cle:
                candidats.append((i, b))
    if not candidats:
        return None
    i, b = hasard.choice(candidats)  # si la ligne revient plusieurs fois (refrain), au hasard
    m = mots(lignes[i][1])
    if "|" in b and len(m) >= 2:
        k = max(1, min(len(mots(b.split("|")[0])), len(m) - 1))
        return i, " ".join(m[:k]), m[k:]
    visible, caches = decouper(lignes[i][1])
    return i, visible, caches


def choisir_cible(lignes: list[tuple[float, str]], hasard=random) -> int:
    """Une ligne d'au moins 3 mots, de préférence dans la seconde moitié de la chanson."""
    ok = [i for i, (_, t) in enumerate(lignes) if len(mots(t)) >= 3 and i >= 1]
    if not ok:
        ok = [i for i, (_, t) in enumerate(lignes) if len(mots(t)) >= 2] or list(range(len(lignes)))
    seconde_moitie = [i for i in ok if i >= len(lignes) // 2]
    return hasard.choice(seconde_moitie or ok)


def decouper(texte: str) -> tuple[str, list[str]]:
    """« il pleut sur la ville » → ("il pleut", ["sur", "la", "ville"]).
    On montre environ le premier tiers des mots (au moins 1) et on cache le reste (au moins 2)."""
    m = mots(texte)
    visibles = max(1, min(len(m) // 3, len(m) - 2)) if len(m) >= 3 else min(1, len(m) - 1)
    return " ".join(m[:visibles]), m[visibles:]


def trous(caches: list[str]) -> str:
    """["sur", "la", "ville,"] → "___ __ _____" (la ponctuation reste visible)."""
    return " ".join(re.sub(r"\w", "_", mot) for mot in caches)


def proche(a: str, b: str) -> bool:
    """Tolère une petite faute de frappe sur les mots assez longs."""
    if a == b:
        return True
    return len(a) >= 5 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def corriger(caches: list[str], reponse: str) -> dict:
    """Compare mot à mot. Renvoie les mots attendus avec juste/faux, et les points."""
    attendus = [normaliser(m) for m in caches]   # un mot affiché peut donner plusieurs jetons (j'ai)
    donnes = normaliser(reponse)
    p = 0
    detail = []
    for mot, jetons in zip(caches, attendus):
        ok = True
        for jeton in jetons:
            # cherche le jeton un peu plus loin si le joueur a ajouté un mot
            trouve = next((k for k in range(p, min(p + 3, len(donnes))) if proche(jeton, donnes[k])), None)
            if trouve is None:
                ok = False
            else:
                p = trouve + 1
        detail.append({"mot": mot, "juste": ok})
    justes = sum(d["juste"] for d in detail)
    parfait = justes == len(detail) and len(donnes) <= sum(len(j) for j in attendus) + 1
    return {
        "detail": detail,
        "parfait": parfait,
        "points": POINTS_PARFAIT if parfait else justes * POINTS_PAR_MOT,
    }


def propositions(caches: list[str], toutes_lignes: list[str], hasard=random) -> list[str]:
    """Le joker : la bonne suite + des leurres pris dans d'autres lignes, de longueur proche."""
    bonne = " ".join(caches)
    n = len(caches)
    vus = {" ".join(normaliser(bonne))}
    leurres = []
    lignes = toutes_lignes[:]
    hasard.shuffle(lignes)
    for ligne in sorted(lignes, key=lambda l: abs(len(mots(l)) - n)):
        m = mots(ligne)
        if len(m) < 2:
            continue
        fin = " ".join(m[-n:]) if len(m) >= n else " ".join(m)
        cle = " ".join(normaliser(fin))
        if cle and cle not in vus:
            vus.add(cle)
            leurres.append(fin)
        if len(leurres) == NB_CHOIX - 1:
            break
    choix = leurres + [bonne]
    hasard.shuffle(choix)
    return choix

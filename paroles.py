#!/usr/bin/env python3
"""Imprime les paroles d'une chanson ligne par ligne, en rythme, sur la cat printer.

Les paroles viennent d'un fichier .lrc (paroles synchronisées) que vous fournissez :
    [ti: titre]
    [00:12.34] première ligne
    [00:15.80] deuxième ligne

Utilisation :
  python paroles.py chanson.lrc --apercu              # test dans le terminal, sans imprimante
  python paroles.py chanson.lrc --spotify             # lance le morceau ([spotify: lien] du .lrc)
  python paroles.py chanson.lrc --spotify https://open.spotify.com/track/…   # lien explicite
  python paroles.py chanson.lrc --spotify suivre      # attend que vous lanciez le morceau
  python paroles.py chanson.lrc --audio chanson.mp3   # joue un fichier local (afplay)
  python paroles.py chanson.lrc                       # appuyez sur Entrée en lançant la chanson
"""

import argparse
import asyncio
import re
import subprocess
import sys
import threading
import time

from PIL import Image, ImageDraw, ImageFont

from coeur import CAT_ECRITURE, CAT_NOTIF, LARGEUR_DEFAUT, cat_paquets, trouver

POLICE_DEFAUT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
CIBLE_DEFAUT = "SC03h"

# ---------------------------------------------------------------- lecture du .lrc

BALISE = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
DECALAGE = re.compile(r"\[offset:\s*([+-]?\d+)\]", re.I)
TITRE = re.compile(r"\[ti:\s*(.*?)\s*\]", re.I)
LIEN = re.compile(r"\[spotify:\s*(.*?)\s*\]", re.I)  # balise maison : lien Spotify du morceau


def lire_lrc(chemin: str) -> tuple[str | None, str | None, list[tuple[float, str]]]:
    lignes, decalage, titre, lien = [], 0.0, None, None
    with open(chemin, encoding="utf-8-sig") as f:
        for brut in f:
            if m := DECALAGE.search(brut):
                decalage = int(m.group(1)) / 1000
                continue
            if m := TITRE.search(brut):
                titre = m.group(1) or None
                continue
            if m := LIEN.search(brut):
                lien = m.group(1) or None
                continue
            temps = BALISE.findall(brut)
            texte = BALISE.sub("", brut).strip()
            if not temps or not texte:
                continue
            for mm, ss in temps:
                lignes.append((int(mm) * 60 + float(ss.replace(":", ".")), texte))
    # Dans le format LRC, un offset positif fait apparaître les paroles plus tôt.
    return titre, lien, sorted((max(0.0, t - decalage), txt) for t, txt in lignes)


# ---------------------------------------------------------------- rendu du texte

def couper(texte, police, largeur):
    morceaux, courante = [], ""
    for mot in texte.split():
        essai = f"{courante} {mot}".strip()
        if police.getlength(essai) <= largeur or not courante:
            courante = essai
        else:
            morceaux.append(courante)
            courante = mot
    return morceaux + [courante] if courante else morceaux


def texte_en_page(texte: str, police, largeur: int, marge=8, interligne=6) -> list[list[bool]]:
    lignes = couper(texte, police, largeur - 2 * marge)
    montee, descente = police.getmetrics()
    h_ligne = montee + descente
    hauteur = len(lignes) * (h_ligne + interligne) + interligne
    img = Image.new("L", (largeur, hauteur), 255)
    d = ImageDraw.Draw(img)
    for k, ligne in enumerate(lignes):
        d.text((marge, interligne + k * (h_ligne + interligne)), ligne, font=police, fill=0)
    px = img.load()
    return [[px[x, y] < 128 for x in range(largeur)] for y in range(hauteur)]


# ---------------------------------------------------------------- horloges
# Une horloge renvoie la position actuelle dans la chanson (en secondes),
# ou None si la chanson n'est pas en cours de lecture.

def osascript(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def uri_spotify(lien: str) -> str:
    """https://open.spotify.com/intl-fr/track/ID?si=… ou spotify:track:ID → spotify:track:ID"""
    if m := re.search(r"track[/:]([A-Za-z0-9]{22})", lien):
        return f"spotify:track:{m.group(1)}"
    sys.exit(f"Lien Spotify de morceau non reconnu : {lien}")


class HorlogeSpotify:
    """Interroge l'app Spotify du Mac (AppleScript) et suit la position de lecture."""

    SCRIPT = ('tell application "Spotify" to return (player state as string) & "|" & '
              '(player position as string) & "|" & (name of current track)')

    def __init__(self, titre: str | None):
        self.titre = titre.lower() if titre else None
        self.etat = None  # (en_lecture, position, nom, instant de la mesure)
        self.erreur = None
        self.actif = True
        threading.Thread(target=self._boucle, daemon=True).start()

    def arreter(self):
        self.actif = False

    def _boucle(self):
        while self.actif:
            try:
                etat, pos, nom = osascript(self.SCRIPT).split("|", 2)
                self.etat = (etat == "playing", float(pos.replace(",", ".")), nom, time.monotonic())
                self.erreur = None
            except Exception as e:  # Spotify fermé, rien en cours, autorisation refusée…
                self.etat, self.erreur = None, e
            time.sleep(0.25)

    def bon_morceau(self, nom: str) -> bool:
        return not self.titre or self.titre in nom.lower() or nom.lower() in self.titre

    def __call__(self):
        if not self.etat:
            return None
        en_lecture, pos, nom, mesure = self.etat
        if not en_lecture or not self.bon_morceau(nom):
            return None
        return pos + (time.monotonic() - mesure)


def demarrer_horloge(args, titre):
    if args.spotify is not None:
        horloge = HorlogeSpotify(titre)
        if args.spotify and args.spotify != "suivre":
            osascript(f'tell application "Spotify" to play track "{uri_spotify(args.spotify)}"')
            if args.debut:
                time.sleep(0.5)
                osascript(f'tell application "Spotify" to set player position to {args.debut}')
            print("Lecture lancée dans Spotify.")
        else:
            print(f"En attente de « {titre or 'la chanson'} » dans Spotify… lancez-la.")
        return horloge
    if args.audio:
        print("Lancement de la musique…")
        subprocess.Popen(["afplay", args.audio])
    else:
        input("Prêt. Appuyez sur Entrée au moment où vous lancez la chanson… ")
    t0 = time.monotonic() - args.debut
    return lambda: time.monotonic() - t0


# ---------------------------------------------------------------- impression en rythme

async def derouler(paroles, envoyer, horloge, args):
    attente_signalee = False
    for t, texte in paroles:
        if t < args.debut:
            continue
        while (pos := horloge()) is None or pos < t - args.avance_temps:
            if pos is None and not attente_signalee and getattr(horloge, "erreur", None):
                print(f"Spotify ne répond pas : {horloge.erreur}")
                attente_signalee = True
            await asyncio.sleep(0.05)
        if pos > t + 3:  # on a avancé dans la chanson : on saute les lignes dépassées
            continue
        print(f"[{int(t // 60):02d}:{t % 60:05.2f}] {texte}")
        await envoyer(texte)


async def imprimer(titre, paroles, args):
    from bleak import BleakClient

    police = ImageFont.truetype(args.police, args.taille)
    d = await trouver(args.cible)
    async with BleakClient(d) as client:
        pret = asyncio.Event()
        pret.set()

        def notif(_, data: bytearray):
            if len(data) > 6 and data[2] == 0xAE:
                pret.clear() if data[6] == 0x10 else pret.set()

        try:
            await client.start_notify(CAT_NOTIF, notif)
        except Exception:
            pass

        async def ecrire(page, avance):
            for paquet in cat_paquets(page, args.energie, avance=avance):
                await pret.wait()
                await client.write_gatt_char(CAT_ECRITURE, paquet, response=False)
                await asyncio.sleep(0.012)

        async def envoyer(texte):
            # On avance le papier après chaque ligne pour qu'elle sorte de sous le capot.
            await ecrire(texte_en_page(texte, police, args.largeur), args.marge)

        print("Imprimante connectée.")
        await derouler(paroles, envoyer, demarrer_horloge(args, titre), args)
        await ecrire([[False] * args.largeur], 120)  # fait sortir le papier pour le déchirer
        await asyncio.sleep(2)
    print("Fin ♥")


async def apercu(titre, paroles, args):
    async def envoyer(_):
        pass
    await derouler(paroles, envoyer, demarrer_horloge(args, titre), args)


def main():
    p = argparse.ArgumentParser(description="Imprimer des paroles synchronisées (.lrc)")
    p.add_argument("lrc", help="fichier de paroles synchronisées .lrc")
    p.add_argument("--cible", default=CIBLE_DEFAUT, help="nom ou adresse BLE de l'imprimante")
    p.add_argument("--spotify", nargs="?", const="", metavar="LIEN",
                   help="lance le morceau dans l'app Spotify (lien en argument ou balise "
                        "[spotify: …] du .lrc) et s'y cale ; « suivre » = ne lance rien, "
                        "attend que vous le lanciez")
    p.add_argument("--audio", help="fichier audio à jouer en même temps (afplay)")
    p.add_argument("--apercu", action="store_true", help="affiche seulement dans le terminal")
    p.add_argument("--avance-temps", type=float, default=0.3,
                   help="envoie chaque ligne N secondes en avance (latence d'impression)")
    p.add_argument("--debut", type=float, default=0.0, help="démarrer à N secondes dans la chanson")
    p.add_argument("--marge", type=int, default=90,
                   help="avance du papier après chaque ligne, en pixels (8 px ≈ 1 mm), "
                        "pour que la ligne soit visible tout de suite")
    p.add_argument("--taille", type=int, default=30, help="taille de police en pixels")
    p.add_argument("--police", default=POLICE_DEFAUT)
    p.add_argument("--largeur", type=int, default=LARGEUR_DEFAUT)
    p.add_argument("--energie", type=lambda v: int(v, 0), default=0x3000)
    args = p.parse_args()

    titre, lien, paroles = lire_lrc(args.lrc)
    if args.spotify == "":
        args.spotify = lien or "suivre"
    if not paroles:
        sys.exit("Aucune ligne horodatée trouvée dans le fichier .lrc.")
    print(f"{len(paroles)} lignes chargées{f' ({titre})' if titre else ''}.")
    asyncio.run((apercu if args.apercu else imprimer)(titre, paroles, args))


if __name__ == "__main__":
    main()

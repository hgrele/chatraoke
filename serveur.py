#!/usr/bin/env python3
"""Interface web : choisir une chanson, la lancer dans Spotify et imprimer les paroles en rythme.

  .venv/bin/python serveur.py          puis http://localhost:8765 (s'ouvre tout seul)
  .venv/bin/python serveur.py --debug  ouvre http://localhost:8765/?debug (sans le rideau d'intro)

Les chansons sont les fichiers .lrc du dossier du projet et du dossier chansons/.
Pour qu'un clic lance la musique, le .lrc doit contenir une ligne [spotify: lien du morceau].
"""

import asyncio
import json
import os
import random
import re
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import ImageFont

from coeur import CAT_ECRITURE, CAT_NOTIF, LARGEUR_DEFAUT, cat_paquets, coeur, coller
from paroles import CIBLE_DEFAUT, POLICE_DEFAUT, HorlogeSpotify, lire_lrc, osascript, texte_en_page
from rideau import dessiner_rideau, rideau_en_page
import jeu

ICI = Path(__file__).resolve().parent
PORT = 8765
AVANCE_TEMPS = 0.7   # s d'avance pour compenser le temps d'impression
SORTIE = 90          # distance tête d'impression → bord de sortie, en rangées (8 px ≈ 1 mm)
ECART = 16           # blanc entre deux lignes de paroles, en rangées
ENERGIE = 0x3000
LARGEUR = LARGEUR_DEFAUT
INTRO_URI = "spotify:track:6fWb6cV6QyodbRatTO7rh5"  # musique du lever de rideau
INTRO_IMPRESSION = 16  # s : durée sur laquelle le rideau est imprimé, pour suivre la musique

ARTISTE = re.compile(r"\[ar:\s*(.*?)\s*\]", re.I)
DUREE = re.compile(r"\[length:\s*(\d+):(\d+)", re.I)
BALISE_JEU = re.compile(r"\[jeu:\s*(.*?)\s*\]", re.I)  # ligne à faire trouver, ex. [jeu: Il pleut | de l'eau d'javel]
PISTE = re.compile(r"track[/:]([A-Za-z0-9]{22})")

# ---------------------------------------------------------------- bibliothèque

_pochettes: dict[str, str | None] = {}


def pochette(lien: str | None) -> str | None:
    """Image de pochette via l'oEmbed public de Spotify (sans compte ni clé)."""
    if not lien:
        return None
    if lien not in _pochettes:
        try:
            url = "https://open.spotify.com/oembed?url=" + urllib.parse.quote(lien, safe="")
            with urllib.request.urlopen(url, timeout=4) as r:
                _pochettes[lien] = json.load(r).get("thumbnail_url")
        except Exception:
            _pochettes[lien] = None
    return _pochettes[lien]


def lister_chansons() -> list[dict]:
    chansons, vus = [], set()
    for f in sorted(ICI.glob("*.lrc")) + sorted((ICI / "chansons").glob("*.lrc")):
        if f.stem in vus:
            continue
        titre, lien, lignes = lire_lrc(f)
        if not lignes:
            continue
        vus.add(f.stem)
        entete = f.read_text(encoding="utf-8-sig")
        ar, du = ARTISTE.search(entete), DUREE.search(entete)
        piste = PISTE.search(lien or "")
        chansons.append({
            "id": f.stem,
            "titre": titre or f.stem,
            "artiste": ar.group(1) if ar else "",
            "uri": f"spotify:track:{piste.group(1)}" if piste else None,
            "pochette": pochette(lien) if piste else None,
            "duree": int(du.group(1)) * 60 + int(du.group(2)) if du else lignes[-1][0] + 10,
            "lignes": lignes,
            "jeu": BALISE_JEU.findall(entete),
        })
    return chansons


# ---------------------------------------------------------------- lecture + impression

class Bande:
    """Bande de papier continue. Le papier n'avance qu'en imprimant : pour faire sortir une ligne,
    on imprime jusqu'à SORTIE rangées sous elle, ce qui la pousse hors de l'imprimante tandis que
    la suite est déjà « pré-imprimée » dedans (pas de grands blancs entre les lignes)."""

    def __init__(self, lecteur: "Lecteur"):
        self.lecteur, self.rangees, self.imprime = lecteur, [], 0

    def ajouter(self, page, ecart=0) -> int:
        """Ajoute une page (et un blanc dessous) ; renvoie la rangée de fin de la page."""
        self.rangees.extend(page)
        fin = len(self.rangees)
        self.rangees.extend([[False] * LARGEUR] * ecart)
        return fin

    async def jusqua(self, n: int):
        n = min(n, len(self.rangees))
        if n > self.imprime:
            await self.lecteur.ecrire(self.rangees[self.imprime:n], 0)
            self.imprime = n

    async def sortir(self, fin: int):
        """Fait sortir de l'imprimante tout ce qui est au-dessus de la rangée `fin`."""
        manque = fin + SORTIE - len(self.rangees)
        if manque > 0:
            self.rangees.extend([[False] * LARGEUR] * manque)
        await self.jusqua(fin + SORTIE)


class Lecteur:
    def __init__(self):
        self.client = None
        self.pret = asyncio.Event()
        self.pret.set()
        self.verrou = asyncio.Lock()
        self.tache: asyncio.Task | None = None
        self.etat = {"statut": "inactif", "message": "", "chanson": None, "position": 0.0,
                     "index": -1, "imprimer": True, "imprimante": False, "imprimante_info": "",
                     "jeu": None, "partie": {"score": 0, "manche": 0}}
        self.partie = {"score": 0, "manche": 0}
        self.manche: dict | None = None
        self.impression: asyncio.Task | None = None
        self.police = ImageFont.truetype(POLICE_DEFAUT, 30)
        self.police_titre = ImageFont.truetype(POLICE_DEFAUT, 44)
        self.police_artiste = ImageFont.truetype(POLICE_DEFAUT.replace(" Bold", ""), 24)

    def maj(self, **champs):
        self.etat = {**self.etat, **champs}

    async def connecter(self):
        from bleak import BleakClient, BleakScanner
        async with self.verrou:
            if self.client and self.client.is_connected:
                return
            self.maj(imprimante=False, imprimante_info="Recherche…")
            try:
                d = await BleakScanner.find_device_by_filter(
                    lambda d, _: bool(d.name) and CIBLE_DEFAUT.lower() in d.name.lower(), timeout=12)
            except Exception as e:
                if "turned off" in str(e) or "POWERED_OFF" in str(e):
                    raise RuntimeError("Le Bluetooth du Mac est désactivé.") from e
                raise
            if not d:
                raise RuntimeError("Imprimante introuvable : est-elle allumée et déconnectée du téléphone ?")
            self.client = BleakClient(d, disconnected_callback=lambda _: self.maj(imprimante=False))
            await self.client.connect()

            def notif(_, data: bytearray):
                if len(data) > 6 and data[2] == 0xAE:
                    self.pret.clear() if data[6] == 0x10 else self.pret.set()

            try:
                await self.client.start_notify(CAT_NOTIF, notif)
            except Exception:
                pass
            self.maj(imprimante=True, imprimante_info="")

    async def ecrire(self, page, avance, delai=0.012):
        for paquet in cat_paquets(page, ENERGIE, avance=avance):
            await asyncio.wait_for(self.pret.wait(), 10)
            await self.client.write_gatt_char(CAT_ECRITURE, paquet, response=False)
            await asyncio.sleep(max(0.012, delai))

    async def imprimer_rideau(self):
        try:
            await self.connecter()
            page = rideau_en_page(LARGEUR)
            # rangées envoyées au compte-gouttes : le rideau s'ouvre au rythme de la musique
            await self.ecrire(page, SORTIE, delai=INTRO_IMPRESSION / len(page))
            self.maj(statut="inactif")
        except asyncio.CancelledError:
            self.maj(statut="inactif")
            raise
        except Exception as e:
            self.maj(statut="inactif", imprimante_info=str(e))

    async def lancer_intro(self, imprimer: bool):
        await self._annuler()
        self.maj(statut="intro", chanson=None, position=0.0, index=-1, imprimer=imprimer, message="")
        await asyncio.to_thread(osascript, f'tell application "Spotify" to play track "{INTRO_URI}"')
        if imprimer:
            self.tache = asyncio.create_task(self.imprimer_rideau())
        else:
            self.maj(statut="inactif")

    # ------------------------------------------------ briques communes

    def entete(self, bande: "Bande", chanson: dict, sous_titre: str | None = None) -> int:
        bande.ajouter(texte_en_page(chanson["titre"], self.police_titre, LARGEUR))
        if chanson["artiste"]:
            bande.ajouter(texte_en_page(chanson["artiste"], self.police_artiste, LARGEUR))
        if sous_titre:
            bande.ajouter(texte_en_page(sous_titre, self.police_artiste, LARGEUR))
        bande.ajouter([], 36)
        return len(bande.rangees)

    async def demarrer_spotify(self, chanson: dict, position: float = 0.0) -> HorlogeSpotify:
        horloge = HorlogeSpotify(chanson["titre"])
        if chanson["uri"]:
            await asyncio.to_thread(osascript, f'tell application "Spotify" to play track "{chanson["uri"]}"')
            if position:
                await asyncio.sleep(0.6)
                await asyncio.to_thread(osascript, f'tell application "Spotify" to set player position to {position:.2f}')
            self.maj(statut="attente", message="Démarrage de Spotify…")
        else:
            self.maj(statut="attente", message="Pas de lien Spotify : lance la chanson dans Spotify.")
        return horloge

    async def attendre(self, horloge: HorlogeSpotify, t: float) -> float:
        """Attend que la chanson atteigne t secondes (en suivant pauses et sauts)."""
        while True:
            pos = horloge()
            if pos is not None:
                self.maj(position=pos, statut="lecture", message="")
                if pos >= t:
                    return pos
            elif self.etat["statut"] == "lecture":
                self.maj(statut="pause")
            await asyncio.sleep(0.05)

    async def ecrire_lignes(self, textes_polices: list[tuple[str, ImageFont.FreeTypeFont]], avance=SORTIE):
        page = []
        for texte, police in textes_polices:
            page += texte_en_page(texte, police, LARGEUR)
        await self.ecrire(page, avance)

    # ------------------------------------------------ karaoké

    async def jouer(self, chanson: dict, imprimer: bool):
        horloge = None
        try:
            self.maj(statut="connexion", chanson=chanson["id"], position=0.0, index=-1,
                     imprimer=imprimer, message="", jeu=None)
            bande, fins = Bande(self), []
            if imprimer:
                debut = self.entete(bande, chanson)
                for _, texte in chanson["lignes"]:
                    fins.append(bande.ajouter(texte_en_page(texte, self.police, LARGEUR), ECART))
                await self.connecter()
                await bande.sortir(debut)  # titre visible, 1re ligne en attente dans l'imprimante

            horloge = await self.demarrer_spotify(chanson)
            for i, (t, _) in enumerate(chanson["lignes"]):
                pos = await self.attendre(horloge, t - AVANCE_TEMPS)
                if pos > t + 3:  # on a avancé dans le morceau : ligne dépassée
                    continue
                if imprimer:
                    await bande.sortir(fins[i])
                self.maj(index=i)

            if imprimer:  # reste de la bande, petit coeur de fin, puis on sort le papier
                await bande.jusqua(len(bande.rangees))
                page = [[False] * LARGEUR for _ in range(72)]
                coller(page, coeur(64), (LARGEUR - 64) // 2, 4)
                await self.ecrire(page, SORTIE + 40)
            self.maj(statut="fin", message="")
        except asyncio.CancelledError:
            self.maj(statut="inactif", message="")
            raise
        except Exception as e:
            self.maj(statut="erreur", message=str(e) or type(e).__name__, imprimante_info=str(e))
        finally:
            if horloge:
                horloge.arreter()

    # ------------------------------------------------ jeu : le chat a mangé les paroles

    def etat_jeu(self, **champs):
        self.maj(jeu={**(self.etat["jeu"] or {}), **champs},
                 partie={"score": self.partie["score"], "manche": self.partie["manche"]})

    async def jouer_manche(self, chanson: dict, imprimer: bool):
        horloge = None
        try:
            lignes = chanson["lignes"]
            choisie = jeu.cible_balisee(lignes, chanson["jeu"])
            if choisie:
                cible, visible, caches = choisie
            else:
                cible = jeu.choisir_cible(lignes)
                visible, caches = jeu.decouper(lignes[cible][1])
            debut_i = max(0, cible - jeu.LIGNES_AVANT)
            depart = max(0.0, lignes[debut_i][0] - 4)
            self.partie["manche"] += 1
            self.manche = {"chanson": chanson, "caches": caches, "joker": False, "choix": None,
                           "repondu": False, "imprimer": imprimer}
            self.maj(statut="connexion", chanson=chanson["id"], position=depart, index=debut_i - 1,
                     imprimer=imprimer, message="", jeu=None)
            self.etat_jeu(manche=self.partie["manche"], phase="ecoute", cible=cible, debut=debut_i,
                          visible=visible, trous=jeu.trous(caches), choix=None, resultat=None)

            bande, fins = Bande(self), {}
            if imprimer:
                await self.finir_impression()
                debut = self.entete(bande, chanson, f"Manche {self.partie['manche']} · le chat a mangé les paroles")
                for i in range(debut_i, cible):
                    fins[i] = bande.ajouter(texte_en_page(lignes[i][1], self.police, LARGEUR), ECART)
                fins[cible] = bande.ajouter(
                    texte_en_page(f"{visible} {jeu.trous(caches)}", self.police, LARGEUR), ECART)
                await self.connecter()
                await bande.sortir(debut)

            horloge = await self.demarrer_spotify(chanson, depart)
            for i in range(debut_i, cible):
                t = lignes[i][0]
                pos = await self.attendre(horloge, t - AVANCE_TEMPS)
                if imprimer and pos <= t + 3:
                    await bande.sortir(fins[i])
                self.maj(index=i)

            # la musique se coupe juste avant la ligne mystère
            await self.attendre(horloge, lignes[cible][0] - 0.25)
            await asyncio.to_thread(osascript, 'tell application "Spotify" to pause')
            self.maj(statut="question")
            self.etat_jeu(phase="question")
            if imprimer:
                await bande.sortir(fins[cible])
        except asyncio.CancelledError:
            self.maj(statut="inactif", message="")
            raise
        except Exception as e:
            self.maj(statut="erreur", message=str(e) or type(e).__name__)
        finally:
            if horloge:
                horloge.arreter()

    def verifier_question(self):
        if not self.manche or self.manche["repondu"] or self.etat["statut"] != "question":
            raise RuntimeError("Pas de question en cours.")

    async def joker(self) -> dict:
        self.verifier_question()
        m = self.manche
        if not m["joker"]:
            chansons = await asyncio.to_thread(lister_chansons)
            toutes = [t for c in chansons for _, t in c["lignes"]]
            m["joker"], m["choix"] = True, jeu.propositions(m["caches"], toutes)
            self.etat_jeu(choix=m["choix"])
        return {"choix": m["choix"]}

    async def repondre(self, texte: str) -> dict:
        self.verifier_question()
        m = self.manche
        m["repondu"] = True
        r = jeu.corriger(m["caches"], texte)
        if m["joker"]:
            r["points"] = jeu.POINTS_JOKER if r["parfait"] else 0
        self.partie["score"] += r["points"]
        resultat = {**r, "attendu": " ".join(m["caches"]), "donne": texte, "joker": m["joker"]}
        self.maj(statut="resultat")
        self.etat_jeu(phase="resultat", resultat=resultat)
        try:  # on relance la musique pour entendre la vraie réponse
            await asyncio.to_thread(osascript, 'tell application "Spotify" to play')
        except Exception:
            pass
        if m["imprimer"]:
            # tâche à part : lancer la manche suivante ne doit pas couper ce ticket
            self.impression = asyncio.create_task(self.imprimer_resultat(resultat))
        return resultat

    async def finir_impression(self):
        if self.impression and not self.impression.done():
            try:
                await self.impression
            except BaseException:
                pass

    async def imprimer_resultat(self, r: dict):
        if r["parfait"]:
            verdict = f"BRAVO ! +{r['points']}"
        elif r["points"]:
            verdict = f"Presque ! +{r['points']}"
        else:
            verdict = "Raté… le chat garde les paroles"
        try:
            await self.ecrire_lignes([(f"> {r['attendu']}", self.police), (verdict, self.police_titre),
                                      (f"Score : {self.partie['score']}", self.police_artiste)])
        except Exception as e:
            self.maj(imprimante_info=str(e))

    async def lancer_manche(self, id_: str | None, imprimer: bool):
        chansons = await asyncio.to_thread(lister_chansons)
        jouables = [c for c in chansons if c["uri"]] or chansons
        if id_:
            chanson = next((c for c in chansons if c["id"] == id_), None)
            if not chanson:
                raise KeyError(f"Chanson inconnue : {id_}")
        elif jouables:
            chanson = random.choice(jouables)
        else:
            raise RuntimeError("Aucune chanson disponible.")
        await self._annuler()
        self.tache = asyncio.create_task(self.jouer_manche(chanson, imprimer))

    async def nouvelle_partie(self):
        await self._annuler()
        self.partie = {"score": 0, "manche": 0}
        self.manche = None
        self.maj(statut="inactif", chanson=None, jeu=None, partie=dict(self.partie))

    async def terminer_partie(self, imprimer: bool):
        await self.arreter()
        score, manches = self.partie["score"], self.partie["manche"]
        self.maj(statut="inactif")
        self.etat_jeu(phase="fin")
        if imprimer and manches:
            async def ticket():
                try:
                    await self.finir_impression()
                    await self.connecter()
                    await self.ecrire_lignes([("Fin de partie", self.police_titre),
                                              (f"Score final : {score}", self.police),
                                              (f"en {manches} manche{'s' if manches > 1 else ''}", self.police_artiste)],
                                             avance=0)
                    page = [[False] * LARGEUR for _ in range(72)]
                    coller(page, coeur(64), (LARGEUR - 64) // 2, 4)
                    await self.ecrire(page, SORTIE + 40)
                except Exception as e:
                    self.maj(imprimante_info=str(e))
            self.tache = asyncio.create_task(ticket())

    async def _annuler(self):
        if self.tache and not self.tache.done():
            self.tache.cancel()
            try:
                await self.tache
            except BaseException:
                pass

    async def lancer(self, id_: str, imprimer: bool):
        chansons = await asyncio.to_thread(lister_chansons)
        chanson = next((c for c in chansons if c["id"] == id_), None)
        if not chanson:
            raise KeyError(f"Chanson inconnue : {id_}")
        await self._annuler()
        self.tache = asyncio.create_task(self.jouer(chanson, imprimer))

    async def arreter(self):
        await self._annuler()
        try:
            await asyncio.to_thread(osascript, 'tell application "Spotify" to pause')
        except Exception:
            pass

    async def connexion_initiale(self):
        try:
            await self.connecter()
        except Exception as e:  # on réessaiera au lancement d'une chanson
            self.maj(imprimante_info=str(e))


# ---------------------------------------------------------------- HTTP

_rideau_png: bytes | None = None


def rideau_png() -> bytes:
    """Le dessin imprimé, pour l'afficher sur le ticket virtuel."""
    global _rideau_png
    if _rideau_png is None:
        import io
        tampon = io.BytesIO()
        dessiner_rideau(LARGEUR).save(tampon, "PNG")
        _rideau_png = tampon.getvalue()
    return _rideau_png

BOUCLE: asyncio.AbstractEventLoop
LECTEUR: Lecteur


class Requetes(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def repondre(self, code, corps, type_="application/json"):
        data = corps if isinstance(corps, bytes) else json.dumps(corps, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", f"{type_}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.path = urllib.parse.urlsplit(self.path).path  # ignore « ?debug » etc.
        if self.path in ("/", "/index.html"):
            self.repondre(200, (ICI / "interface.html").read_bytes(), "text/html")
        elif self.path == "/api/chansons":
            self.repondre(200, lister_chansons())
        elif self.path == "/api/etat":
            self.repondre(200, LECTEUR.etat)
        elif self.path == "/api/rideau.png":
            self.repondre(200, rideau_png(), "image/png")
        else:
            self.repondre(404, {"erreur": "introuvable"})

    def do_POST(self):
        try:
            corps = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/lancer":
                action = LECTEUR.lancer(str(corps["id"]), bool(corps.get("imprimer", True)))
            elif self.path == "/api/intro":
                action = LECTEUR.lancer_intro(bool(corps.get("imprimer", True)))
            elif self.path == "/api/arreter":
                action = LECTEUR.arreter()
            elif self.path == "/api/connecter":
                BOUCLE.call_soon_threadsafe(lambda: asyncio.create_task(LECTEUR.connexion_initiale()))
                return self.repondre(200, {"ok": True})
            elif self.path == "/api/jeu/manche":
                action = LECTEUR.lancer_manche(corps.get("id"), bool(corps.get("imprimer", True)))
            elif self.path == "/api/jeu/joker":
                action = LECTEUR.joker()
            elif self.path == "/api/jeu/repondre":
                action = LECTEUR.repondre(str(corps.get("texte", "")))
            elif self.path == "/api/jeu/nouvelle":
                action = LECTEUR.nouvelle_partie()
            elif self.path == "/api/jeu/terminer":
                action = LECTEUR.terminer_partie(bool(corps.get("imprimer", True)))
            else:
                return self.repondre(404, {"erreur": "introuvable"})
            resultat = asyncio.run_coroutine_threadsafe(action, BOUCLE).result(timeout=20)
            self.repondre(200, resultat if isinstance(resultat, dict) else {"ok": True})
        except Exception as e:
            self.repondre(400, {"erreur": str(e) or type(e).__name__})


async def principal():
    global BOUCLE, LECTEUR
    BOUCLE = asyncio.get_running_loop()
    LECTEUR = Lecteur()
    serveur = ThreadingHTTPServer(("127.0.0.1", PORT), Requetes)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    url = f"http://localhost:{PORT}" + ("/?debug" if "--debug" in sys.argv else "")
    print(f"Interface : {url}   (Ctrl+C pour quitter)")
    if not os.environ.get("SANS_NAVIGATEUR"):
        subprocess.Popen(["open", url])
    asyncio.create_task(LECTEUR.connexion_initiale())
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(principal())
    except KeyboardInterrupt:
        print("\nÀ bientôt ♥")

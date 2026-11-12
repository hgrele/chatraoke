#!/usr/bin/env python3
"""Imprime des coeurs sur une imprimante thermique Bluetooth, sans l'application du fabricant.

Gère les deux familles les plus courantes :
  * « cat printers » BLE (GB01/GB02/GB03, MX05/MX06/MX10, YT01…) — service 0xAE30
  * imprimantes de tickets ESC/POS (BLE ou Bluetooth classique via /dev/cu.*)

Utilisation :
  python coeurs.py apercu                     # affiche les coeurs dans le terminal (sans imprimante)
  python coeurs.py scan                       # liste les appareils Bluetooth LE à proximité
  python coeurs.py inspecter <adresse|nom>    # liste les services et devine le protocole
  python coeurs.py imprimer <adresse|nom>     # imprime les coeurs
  python coeurs.py imprimer --serie /dev/cu.XXX   # Bluetooth classique (après appairage macOS)
"""

import argparse
import asyncio
import sys

LARGEUR_DEFAUT = 384  # 58 mm à 203 dpi ; mettre 576 pour une imprimante 80 mm


# ---------------------------------------------------------------- dessin

def dans_coeur(x: float, y: float) -> bool:
    # Courbe du coeur : (x² + y² - 1)³ - x²·y³ <= 0
    return (x * x + y * y - 1) ** 3 - x * x * y ** 3 <= 0


def coeur(taille: int) -> list[list[bool]]:
    """Bitmap carré d'un coeur plein, taille x taille pixels."""
    rangees = []
    for j in range(taille):
        y = 1.25 - 2.45 * j / (taille - 1)
        rangees.append([dans_coeur(-1.25 + 2.5 * i / (taille - 1), y) for i in range(taille)])
    return rangees


def coller(page, motif, x0, y0):
    for j, rangee in enumerate(motif):
        for i, p in enumerate(rangee):
            if p and 0 <= y0 + j < len(page) and 0 <= x0 + i < len(page[0]):
                page[y0 + j][x0 + i] = True


def dessiner_coeurs(largeur: int) -> list[list[bool]]:
    """Un grand coeur, puis une rangée de petits coeurs."""
    grand = int(largeur * 0.8)
    nb_petits = 5
    petit = largeur // (nb_petits + 1)
    marge = 20
    hauteur = grand + marge + petit + marge
    page = [[False] * largeur for _ in range(hauteur)]
    coller(page, coeur(grand), (largeur - grand) // 2, 0)
    pas = largeur // nb_petits
    for k in range(nb_petits):
        coller(page, coeur(petit), k * pas + (pas - petit) // 2, grand + marge)
    return page


def apercu(page, echelle=6):
    for j in range(0, len(page), echelle * 2):
        print("".join("█" if page[j][i] else " " for i in range(0, len(page[0]), echelle)))


def octets_rangee(rangee, lsb_first: bool) -> bytes:
    out = bytearray(len(rangee) // 8)
    for i, p in enumerate(rangee):
        if p:
            out[i // 8] |= (1 << (i % 8)) if lsb_first else (0x80 >> (i % 8))
    return bytes(out)


# ---------------------------------------------------------------- protocole « cat printer »

CRC8_TABLE = []
for _b in range(256):
    _c = _b
    for _ in range(8):
        _c = ((_c << 1) ^ 0x07) & 0xFF if _c & 0x80 else (_c << 1) & 0xFF
    CRC8_TABLE.append(_c)


def crc8(data: bytes) -> int:
    c = 0
    for b in data:
        c = CRC8_TABLE[c ^ b]
    return c


def cat_cmd(cmd: int, data: bytes) -> bytes:
    n = len(data)
    return bytes([0x51, 0x78, cmd, 0x00, n & 0xFF, n >> 8]) + data + bytes([crc8(data), 0xFF])


def cat_paquets(page, energie=0x3000, avance=80):
    yield cat_cmd(0xA3, b"\x00")                      # état
    yield cat_cmd(0xA4, b"\x33")                      # qualité
    yield cat_cmd(0xAF, energie.to_bytes(2, "little"))  # énergie (noirceur)
    yield cat_cmd(0xBE, b"\x00")                      # mode image
    yield cat_cmd(0xBD, b"\x20")                      # vitesse
    yield cat_cmd(0xA6, bytes([0xAA, 0x55, 0x17, 0x38, 0x44, 0x5F, 0x5F, 0x5F, 0x44, 0x38, 0x2C]))
    for rangee in page:
        yield cat_cmd(0xA2, octets_rangee(rangee, lsb_first=True))
    if avance:
        yield cat_cmd(0xA1, avance.to_bytes(2, "little"))  # avance du papier
    yield cat_cmd(0xA6, bytes([0xAA, 0x55, 0x17, 0, 0, 0, 0, 0, 0, 0, 0x17]))
    yield cat_cmd(0xA3, b"\x00")


# ---------------------------------------------------------------- protocole ESC/POS

def escpos_octets(page, bande=24) -> bytes:
    largeur_octets = len(page[0]) // 8
    out = bytearray(b"\x1b\x40")  # ESC @ : initialisation
    for debut in range(0, len(page), bande):
        bloc = page[debut:debut + bande]
        out += b"\x1d\x76\x30\x00"  # GS v 0 : image raster
        out += largeur_octets.to_bytes(2, "little") + len(bloc).to_bytes(2, "little")
        for rangee in bloc:
            out += octets_rangee(rangee, lsb_first=False)
    out += b"\x1b\x64\x05"  # ESC d 5 : avance de 5 lignes
    return bytes(out)


# ---------------------------------------------------------------- Bluetooth LE

CAT_ECRITURE = "0000ae01-0000-1000-8000-00805f9b34fb"
CAT_NOTIF = "0000ae02-0000-1000-8000-00805f9b34fb"
ESCPOS_CONNUES = [
    "49535343-8841-43f4-a8d4-ecbe34729bb3",
    "0000ff02-0000-1000-8000-00805f9b34fb",
    "00002af1-0000-1000-8000-00805f9b34fb",
    "0000fff2-0000-1000-8000-00805f9b34fb",
    "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f",
    "e7810a71-73ae-499d-8c15-faa9aef0c3f2",
]
SERVICES_STANDARD = ("00001800", "00001801", "0000180a", "0000180f")


async def trouver(cible: str, duree=8.0):
    from bleak import BleakScanner
    print(f"Recherche de « {cible} »…")
    appareils = await BleakScanner.discover(timeout=duree)
    for d in appareils:
        if d.address.lower() == cible.lower() or (d.name and cible.lower() in d.name.lower()):
            print(f"Trouvé : {d.name} [{d.address}]")
            return d
    sys.exit("Introuvable. Vérifiez qu'elle est allumée et pas connectée au téléphone.")


async def cmd_scan(args):
    from bleak import BleakScanner
    print(f"Scan Bluetooth LE pendant {args.duree:.0f} s… (allumez l'imprimante)")
    res = await BleakScanner.discover(timeout=args.duree, return_adv=True)
    lignes = sorted(res.values(), key=lambda da: -da[1].rssi)
    for d, adv in lignes:
        services = ", ".join(u[4:8] for u in adv.service_uuids) or "-"
        print(f"{adv.rssi:>5} dBm  {d.name or '(sans nom)':<24} {d.address}   services: {services}")
    print("\nAstuce : éteignez puis rallumez l'imprimante et relancez le scan ; "
          "l'appareil qui apparaît/disparaît, c'est elle.")


def deviner(client):
    """Renvoie (protocole, uuid_ecriture)."""
    ecritures = []
    for s in client.services:
        for c in s.characteristics:
            if {"write", "write-without-response"} & set(c.properties):
                ecritures.append((s.uuid, c.uuid))
    uuids = [c for _, c in ecritures]
    if CAT_ECRITURE in uuids:
        return "cat", CAT_ECRITURE
    for u in ESCPOS_CONNUES:
        if u in uuids:
            return "escpos", u
    for s, c in ecritures:
        if not s.startswith(SERVICES_STANDARD):
            return "escpos?", c
    return None, None


async def cmd_inspecter(args):
    from bleak import BleakClient
    d = await trouver(args.cible)
    async with BleakClient(d) as client:
        for s in client.services:
            print(f"Service {s.uuid}  {s.description}")
            for c in s.characteristics:
                print(f"   └ {c.uuid}  {','.join(c.properties)}")
        proto, uuid = deviner(client)
        print(f"\nProtocole probable : {proto or 'inconnu'}  (écriture sur {uuid})")
        if d.name and d.name.upper().startswith("MXW"):
            print("Attention : les MXW01 utilisent un protocole différent, non géré par ce script.")


async def cmd_imprimer_ble(args, page):
    from bleak import BleakClient
    d = await trouver(args.cible)
    async with BleakClient(d) as client:
        proto, uuid = deviner(client)
        if args.mode != "auto":
            proto = args.mode
        if not proto:
            sys.exit("Aucune caractéristique d'écriture trouvée. Lancez « inspecter » et envoyez-moi le résultat.")
        print(f"Protocole : {proto}  → {uuid}")
        car = client.services.get_characteristic(uuid)
        sans_reponse = "write-without-response" in car.properties

        if proto == "cat":
            pret = asyncio.Event()
            pret.set()

            def notif(_, data: bytearray):
                # 51 78 AE ... 10 = buffer plein (pause), 00 = reprise
                if len(data) > 6 and data[2] == 0xAE:
                    pret.clear() if data[6] == 0x10 else pret.set()

            try:
                await client.start_notify(CAT_NOTIF, notif)
            except Exception:
                pass
            for paquet in cat_paquets(page, args.energie):
                await pret.wait()
                await client.write_gatt_char(uuid, paquet, response=False)
                await asyncio.sleep(0.012)
        else:
            donnees = escpos_octets(page)
            taille = max(20, min(180, (client.mtu_size or 23) - 3))
            for i in range(0, len(donnees), taille):
                await client.write_gatt_char(uuid, donnees[i:i + taille], response=not sans_reponse)
                await asyncio.sleep(0.02)
        await asyncio.sleep(2)
    print("Envoyé ! ♥")


def cmd_imprimer_serie(args, page):
    import serial
    with serial.Serial(args.serie, 9600, timeout=5) as port:
        port.write(escpos_octets(page))
        port.flush()
    print("Envoyé ! ♥")


def main():
    p = argparse.ArgumentParser(description="Imprimer des coeurs sur une imprimante thermique Bluetooth")
    p.add_argument("--largeur", type=int, default=LARGEUR_DEFAUT, help="384 (58 mm) ou 576 (80 mm)")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("apercu")
    s = sub.add_parser("scan")
    s.add_argument("--duree", type=float, default=10)
    i = sub.add_parser("inspecter")
    i.add_argument("cible", help="adresse/UUID ou partie du nom")
    im = sub.add_parser("imprimer")
    im.add_argument("cible", nargs="?", help="adresse/UUID ou partie du nom (BLE)")
    im.add_argument("--serie", help="port Bluetooth classique, ex. /dev/cu.Printer001")
    im.add_argument("--mode", choices=["auto", "cat", "escpos"], default="auto")
    im.add_argument("--energie", type=lambda v: int(v, 0), default=0x3000,
                    help="noirceur pour les cat printers (ex. 0x2000 à 0xFFFF)")
    args = p.parse_args()

    page = dessiner_coeurs(args.largeur)
    if args.action == "apercu":
        apercu(page)
    elif args.action == "scan":
        asyncio.run(cmd_scan(args))
    elif args.action == "inspecter":
        asyncio.run(cmd_inspecter(args))
    elif args.serie:
        cmd_imprimer_serie(args, page)
    elif args.cible:
        asyncio.run(cmd_imprimer_ble(args, page))
    else:
        p.error("indiquez une cible BLE ou --serie /dev/cu.XXX")


if __name__ == "__main__":
    main()

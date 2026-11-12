#!/usr/bin/env python3
"""Dessin de l'intro : un rideau de théâtre qui s'ouvre au fil du papier et révèle « ChatRaoké ».

  python rideau.py apercu.png      # enregistre le dessin pour le regarder
"""

import math
import sys

from PIL import Image, ImageDraw, ImageFont

POLICE_TITRE = "/System/Library/Fonts/Supplemental/Arial Black.ttf"

# Matrice de Bayer 4x4 : simule des gris avec des points noirs et blancs (tramage)
BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def trame(x, y, clarte):
    """True = point noir, pour une clarté entre 0 (noir) et 1 (blanc)."""
    return clarte <= (BAYER[y % 4][x % 4] + 0.5) / 16


def dessiner_rideau(largeur=384, texte="ChatRaoké") -> Image.Image:
    W = largeur
    H_LAMBREQUIN = 64   # bandeau du haut avec ampoules et festons
    H_FERME = 150       # rideau fermé (roulement de tambour)
    H_OUVERTURE = 380   # le rideau s'ouvre
    H_SCENE = 270       # scène ouverte avec le titre
    H = H_LAMBREQUIN + H_FERME + H_OUVERTURE + H_SCENE
    RESTE = 30          # largeur de chaque pan de rideau une fois ouvert
    PLIS = 7

    img = Image.new("L", (W, H), 255)
    px = img.load()

    def bord(y):
        """Abscisse du bord intérieur du pan gauche à la rangée y."""
        p = (y - H_LAMBREQUIN - H_FERME) / H_OUVERTURE
        p = min(1.0, max(0.0, p))
        p = p * p * (3 - 2 * p)  # ouverture douce au début et à la fin
        return W / 2 - (W / 2 - RESTE) * p

    # --- les deux pans de velours, avec des plis qui se resserrent en s'ouvrant
    for y in range(H_LAMBREQUIN - 10, H):
        xl = bord(y)
        for x in range(W):
            if x < xl:
                u, d = x / xl, xl - x
            elif x >= W - xl:
                u, d = (W - 1 - x) / xl, x - (W - xl)
            else:
                continue
            if d < 2.5:  # liseré blanc sur le bord intérieur
                continue
            v = (math.cos(u * PLIS * 2 * math.pi) + 1) / 2   # 1 = sommet du pli
            clarte = 0.04 + 0.5 * v ** 4
            if trame(x, y, clarte):
                px[x, y] = 0

    d = ImageDraw.Draw(img)

    # --- lambrequin : bandeau sombre + festons + ampoules
    d.rectangle([0, 0, W, H_LAMBREQUIN - 22], fill=0)
    rayon = 24
    for cx in range(rayon, W + rayon, 2 * rayon):
        d.ellipse([cx - rayon, H_LAMBREQUIN - 22 - rayon, cx + rayon, H_LAMBREQUIN - 22 + rayon], fill=0)
        d.ellipse([cx - 4, H_LAMBREQUIN - 22 + rayon - 2, cx + 4, H_LAMBREQUIN - 22 + rayon + 6], fill=0)  # pompon
    for cx in range(16, W, 32):
        d.ellipse([cx - 6, 10, cx + 6, 22], fill=255)

    # --- scène : chat, titre, plancher
    haut_scene = H_LAMBREQUIN + H_FERME + H_OUVERTURE
    utile = W - 2 * RESTE - 24
    taille = 80
    while taille > 20:
        police = ImageFont.truetype(POLICE_TITRE, taille)
        if police.getlength(texte) <= utile:
            break
        taille -= 2

    # petite tête de chat
    cx, cy, r = W // 2, haut_scene + 30 + 34, 34
    d.polygon([(cx - r + 4, cy - 12), (cx - r + 8, cy - r - 22), (cx - 8, cy - r + 2)], fill=0)
    d.polygon([(cx + r - 4, cy - 12), (cx + r - 8, cy - r - 22), (cx + 8, cy - r + 2)], fill=0)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=0)
    for ox in (-13, 13):  # yeux
        d.ellipse([cx + ox - 7, cy - 10, cx + ox + 7, cy + 4], fill=255)
        d.ellipse([cx + ox - 3, cy - 7, cx + ox + 3, cy + 3], fill=0)
    d.polygon([(cx - 5, cy + 9), (cx + 5, cy + 9), (cx, cy + 15)], fill=255)  # nez
    for k in (-1, 1):  # moustaches
        for dy in (-4, 4):
            d.line([(cx + k * 22, cy + 14 + dy // 2), (cx + k * (r + 22), cy + 10 + dy * 2)], fill=0, width=2)

    ascent, descent = police.getmetrics()
    ty = cy + r + 26
    d.text(((W - police.getlength(texte)) / 2, ty), texte, font=police, fill=0)

    # étincelles autour du titre
    for sx, sy, s in [(RESTE + 26, ty - 6, 7), (W - RESTE - 26, ty - 6, 7),
                      (cx - 78, cy - 30, 5), (cx + 78, cy - 30, 5)]:
        d.line([(sx - s, sy), (sx + s, sy)], fill=0, width=2)
        d.line([(sx, sy - s), (sx, sy + s)], fill=0, width=2)

    # plancher de la scène
    sol = H - 22
    d.rectangle([0, sol, W, sol + 4], fill=0)
    for x in range(0, W, 24):
        d.line([(x, sol + 4), (x - 10, H)], fill=0, width=1)
    return img


def rideau_en_page(largeur=384, texte="ChatRaoké") -> list[list[bool]]:
    img = dessiner_rideau(largeur, texte)
    px = img.load()
    return [[px[x, y] < 128 for x in range(img.width)] for y in range(img.height)]


if __name__ == "__main__":
    dessiner_rideau().save(sys.argv[1] if len(sys.argv) > 1 else "rideau.png")

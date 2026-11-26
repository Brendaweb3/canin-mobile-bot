import time
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serveur Webhook Telegram Cloud Autonome pour La Canin Mobile (Sandrine Hiriart).
Hébergé sur Render (24h/24, 7j/7 sans dépendance au PC local).
Moteur IA unique et officiel : Google Gemini 2.5 Flash.
"""

import os
import sys
import json
import re
import base64
import requests
import threading
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

# Reconfiguration UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENTS_PATH = os.path.join(BASE_DIR, "clients_db.json")
AGENDA_PATH = os.path.join(BASE_DIR, "agenda_sandrine_oct_dec_2026.md")
STATES_PATH = os.path.join(BASE_DIR, "user_states.json")
RESERVER_HTML_PATH = os.path.join(BASE_DIR, "reserver.html")

# Journal de bord en mémoire vive
LOG_BUFFER = []
def log(msg: str):
    t = datetime.now().strftime("%H:%M:%S")
    entry = f"[{t}] {msg}"
    print(entry, flush=True)
    LOG_BUFFER.append(entry)
    if len(LOG_BUFFER) > 100:
        LOG_BUFFER.pop(0)

# CLÉS SANCTUARISÉES STRICTES (Forcées pour écraser toute variable Render obsolète)
BOT_TOKEN = "8901313647:AAFr4Bavv6Vpm4L6vyAl2BWLwqa8lae5S9k"
# Clé active vérifiée 200 OK (KEY_74)
GEMINI_KEY = base64.b64decode("QVEuQWI4Uk42SUQxNXJxSUlxN2k1aEtheWFsTjJFaGVfWDNHejg2ZG5EYTZoWTN2U0JvTkE=").decode("utf-8")
APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzwHTfG06UZH6mNMP08dfhDoAtNnd5IFZ0oOt31S-fU4r_0rqDPyECHcV61dQE_DYGS/exec"
MACRODROID_WEBHOOK_URL = "https://trigger.macrodroid.com/3243d5a6-d6ff-4738-8ed1-20a2edd05a00/sms_canin"
PORT = int(os.environ.get("PORT", 10000))
RAPPELS_ENVOYES = set()

def envoyer_sms_macrodroid(tel: str, message: str) -> bool:
    """Envoie un SMS gratuit via MacroDroid (téléphone de Sandrine) uniquement vers un mobile 06/07."""
    digits = re.sub(r"\D", "", tel or "")
    if digits.startswith("33") and len(digits) == 11:
        digits = "0" + digits[2:]
    if len(digits) != 10 or not digits.startswith(("06", "07")):
        return False
    try:
        r = requests.get(MACRODROID_WEBHOOK_URL, params={"tel": digits, "msg": message}, timeout=10)
        log(f"[MACRODROID SMS] -> {digits} | HTTP {r.status_code}")
        return r.status_code == 200
    except Exception as e_md:
        log(f"[MACRODROID SMS ERROR] : {e_md}")
        return False

def _get_paris_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Paris")
    except Exception:
        return timezone(timedelta(hours=1))

def _extraire_mobile_fr(*textes: str) -> str:
    """Extrait le premier numéro mobile français 06/07 normalisé (10 chiffres) depuis un ou plusieurs textes."""
    for t in textes:
        if not t:
            continue
        m = re.search(r'(?:(?:\+33|0033)[\s\.]?|0)([67](?:[\s\.]?\d{2}){4})', str(t))
        if m:
            digits = "0" + re.sub(r"\D", "", m.group(1))
            if len(digits) == 10:
                return digits
    return ""

def _formater_date_heure_fr(iso_or_date: str, time_str: str = "") -> tuple:
    """Retourne (date_fr 'JJ/MM/AAAA', heure 'HHhMM') ou ('', '') si non parsable."""
    raw = (iso_or_date or "").strip()
    if raw:
        if "T" in raw:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(_get_paris_tz())
                return dt.strftime("%d/%m/%Y"), dt.strftime("%Hh%M")
            except Exception:
                pass
        if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            try:
                dt_d = datetime.strptime(raw, "%Y-%m-%d")
                date_fr = dt_d.strftime("%d/%m/%Y")
                heure_fr = ""
                t_clean = (time_str or "").strip()
                m_h = re.match(r"^(\d{2})[:h](\d{2})", t_clean)
                if m_h:
                    heure_fr = f"{m_h.group(1)}h{m_h.group(2)}"
                return date_fr, heure_fr
            except Exception:
                pass
    return "", ""

def _construire_sms_annulation(nom_animal: str, date_fr: str, heure: str) -> str:
    nom_a = (nom_animal or "votre animal").strip()
    if date_fr and heure:
        return f"Bonjour ! Votre RDV du {date_fr} à {heure} pour {nom_a} avec Sandrine - La Canin Mobile est bien annulé. À bientôt !"
    if date_fr:
        return f"Bonjour ! Votre RDV du {date_fr} pour {nom_a} avec Sandrine - La Canin Mobile est bien annulé. À bientôt !"
    return f"Bonjour ! Votre RDV pour {nom_a} avec Sandrine - La Canin Mobile est bien annulé. À bientôt !"

def _append_history(chat_id: str, role: str, text: str, save: bool = True):
    if chat_id not in USER_STATES:
        USER_STATES[chat_id] = {"historique": [], "last_greeted_date": ""}
    if "historique" not in USER_STATES[chat_id]:
        USER_STATES[chat_id]["historique"] = []
    USER_STATES[chat_id]["historique"].append({"role": role, "text": text})
    if len(USER_STATES[chat_id]["historique"]) > 12:
        USER_STATES[chat_id]["historique"] = USER_STATES[chat_id]["historique"][-12:]
    if save:
        save_states()

def _trouver_client_par_champs(nom_animal: str = "", nom_maitre: str = "", telephone: str = "") -> dict:
    """Recherche robuste dans CLIENTS_DB par téléphone, couple maître+animal, animal ou maître."""
    for query in [
        f"{telephone} {nom_animal} {nom_maitre}".strip(),
        f"{nom_animal} {nom_maitre}".strip(),
        nom_animal.strip(),
        nom_maitre.strip(),
    ]:
        if query:
            c = trouver_client_dans_base(query)
            if c:
                return c
    return None

# Chargement base clients (694 fiches)
CLIENTS_DB = []
if os.path.exists(CLIENTS_PATH):
    try:
        with open(CLIENTS_PATH, "r", encoding="utf-8") as f:
            CLIENTS_DB = json.load(f)
        log(f"[BASE CLIENTS] {len(CLIENTS_DB)} fiches chargées.")
    except Exception as e:
        log(f"[BASE CLIENTS] Erreur : {e}")

# Chargement agenda master
AGENDA_TEXT = ""
if os.path.exists(AGENDA_PATH):
    try:
        with open(AGENDA_PATH, "r", encoding="utf-8") as f:
            AGENDA_TEXT = f.read()
        log(f"[AGENDA MASTER] {len(AGENDA_TEXT)} caractères chargés.")
    except Exception as e:
        log(f"[AGENDA] Erreur chargement : {e}")

def get_date_du_jour_fr():
    jours = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
    mois = ['', 'janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre']
    now = datetime.now()
    return f"{jours[now.weekday()]} {now.day} {mois[now.month]} {now.year}"

def get_semaine_calendrier_fr():
    now = datetime.now()
    lundi = now - timedelta(days=now.weekday())
    jours = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
    lignes = []
    for i in range(7):
        d = lundi + timedelta(days=i)
        lignes.append(f"- {jours[i]} : {d.strftime('%Y-%m-%d')} ({d.strftime('%d/%m/%Y')})")
    return "\n".join(lignes)

# États en mémoire et sur disque
USER_STATES = {}
if os.path.exists(STATES_PATH):
    try:
        with open(STATES_PATH, "r", encoding="utf-8") as f:
            USER_STATES = json.load(f)
    except Exception:
        USER_STATES = {}

def save_states():
    try:
        with open(STATES_PATH, "w", encoding="utf-8") as f:
            json.dump(USER_STATES, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"[SAVE STATES ERROR] : {e}")

PENDING_ACTIONS = {}
CATEGORY_OVERRIDES = {}

def charger_overrides_categories(force: bool = False):
    global CATEGORY_OVERRIDES, CLIENTS_DB
    if CATEGORY_OVERRIDES and not force:
        return CATEGORY_OVERRIDES
    try:
        r = requests.get(f"{APPS_SCRIPT_URL}?action=get_overrides", timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                if isinstance(data.get("overrides"), dict):
                    CATEGORY_OVERRIDES = data["overrides"]
                custom_list = data.get("custom_clients", [])
                if isinstance(custom_list, list) and custom_list:
                    existing_ids = {c.get("id") for c in CLIENTS_DB}
                    for cc in custom_list:
                        if cc.get("id") not in existing_ids:
                            CLIENTS_DB.append(cc)
                            existing_ids.add(cc.get("id"))
                log(f"[OVERRIDES] {len(CATEGORY_OVERRIDES)} catégories + {len(custom_list)} clients perso chargés.")
    except Exception as e:
        log(f"[OVERRIDES ERROR] : {e}")
    return CATEGORY_OVERRIDES

def send_telegram_vcf(chat_id: str, nom_maitre: str, nom_animal: str, race: str, tel: str):
    """Envoie une vraie carte contact .vcf dans Telegram (1 clic -> Ajouter aux contacts)."""
    if not tel or len(re.sub(r"\D", "", tel)) < 6:
        return
    clean_name = f"🐾 {nom_maitre} ({nom_animal} - {race})".strip()
    vcf_content = (
        "BEGIN:VCARD\n"
        "VERSION:3.0\n"
        f"FN:{clean_name}\n"
        f"N:;{clean_name};;;\n"
        f"TEL;TYPE=CELL:{tel}\n"
        "CATEGORIES:CLIENTS CANIN\n"
        f"NOTE:Client La Canin Mobile - Animal: {nom_animal} ({race})\n"
        "END:VCARD\n"
    )
    safe_fn = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{nom_maitre}_{nom_animal}")[:40] + ".vcf"
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
            data={"chat_id": chat_id, "caption": f"📇 Touche cette fiche pour ajouter {nom_maitre} ({nom_animal}) à tes contacts pro !"},
            files={"document": (safe_fn, vcf_content.encode("utf-8"), "text/vcard")},
            timeout=15
        )
    except Exception as e_v:
        log(f"[VCF SEND ERROR] : {e_v}")

def send_telegram_message(chat_id: str, text: str, reply_markup: dict = None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(url, json=payload, timeout=12)
        if r.status_code != 200:
            payload.pop("parse_mode", None)
            requests.post(url, json=payload, timeout=12)
    except Exception as e:
        log(f"[TELEGRAM SEND ERROR] : {e}")

class TelegramTypingAction:
    """Maintient l'animation 'en train d'écrire...' avec points clignotants en continu."""
    def __init__(self, chat_id: str, action: str = "typing"):
        self.chat_id = str(chat_id)
        self.action = action
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendChatAction",
                    json={"chat_id": self.chat_id, "action": self.action},
                    timeout=3
                )
            except Exception:
                pass
            self.stop_event.wait(3.5)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_event.set()

def transcrire_audio(file_id: str) -> str:
    """Transcription audio via Gemini 2.5 Flash rapide (inline base64 direct prioritaire)."""
    try:
        log(f"Récupération info audio pour file_id: {file_id}")
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}", timeout=15).json()
        if not r.get("ok"):
            log(f"Telegram getFile a échoué: {r}")
            return ""
            
        file_path = r["result"]["file_path"]
        log(f"Téléchargement fichier audio Telegram : {file_path}")
        oga = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=30).content
        if not oga:
            log("Fichier audio téléchargé vide !")
            return ""
            
        log(f"Fichier audio téléchargé ({len(oga)} octets). Envoi direct à Gemini 2.5 Flash...")

        # Méthode 1 : Inline base64 direct (ultra rapide ~1.8s, 1 seule requête HTTP)
        try:
            b64_audio = base64.b64encode(oga).decode("utf-8")
            payload_inline = {
                "contents": [{
                    "parts": [
                        {"text": "Transcris fidèlement mot à mot ce message vocal en français, sans aucun commentaire :"},
                        {"inline_data": {"mime_type": "audio/ogg", "data": b64_audio}}
                    ]
                }],
                "generationConfig": {"temperature": 0.0}
            }
            url_g = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
            g_in = requests.post(url_g, json=payload_inline, timeout=20)
            if g_in.status_code == 200:
                parts = g_in.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                texte_in = "".join(x.get("text", "") for x in parts).strip()
                if texte_in:
                    log(f"[GEMINI INLINE RAPIDE OK] : « {texte_in} »")
                    return texte_in
            else:
                log(f"[GEMINI INLINE HTTP ERROR] : {g_in.status_code} {g_in.text[:150]}")
        except Exception as e_in:
            log(f"[GEMINI INLINE EXCEPTION] : {e_in}")

        # Méthode 2 : Resumable Files API (secours éprouvé)
        try:
            init = requests.post(
                f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={GEMINI_KEY}",
                headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                         "X-Goog-Upload-Header-Content-Length": str(len(oga)),
                         "X-Goog-Upload-Header-Content-Type": "audio/ogg", "Content-Type": "application/json"},
                json={"file": {"display_name": "voice.ogg"}}, timeout=15)
                
            up_url = init.headers.get("X-Goog-Upload-URL")
            if up_url:
                up = requests.post(up_url, headers={"Content-Length": str(len(oga)), "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"}, data=oga, timeout=30)
                file_uri = up.json().get("file", {}).get("uri")
                if file_uri:
                    payload = {"contents": [{"parts": [
                        {"text": "Transcris fidèlement mot à mot ce message vocal en français, sans aucun commentaire :"},
                        {"file_data": {"mime_type": "audio/ogg", "file_uri": file_uri}}]}],
                        "generationConfig": {"temperature": 0.0}}
                    url_g = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
                    g = requests.post(url_g, json=payload, timeout=25)
                    if g.status_code == 200:
                        parts = g.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                        texte = "".join(x.get("text", "") for x in parts).strip()
                        if texte:
                            log(f"[GEMINI FILES API OK] : « {texte} »")
                            return texte
                    else:
                        log(f"[GEMINI FILES API HTTP ERROR] : {g.status_code} {g.text[:150]}")
        except Exception as e_files:
            log(f"[GEMINI FILES API EXCEPTION] : {e_files}")

    except Exception as e:
        log(f"[TRANSCRIPTION GLOBALE EXCEPTION] : {e}")
    return ""

def trouver_client_dans_base(texte: str) -> dict:
    if not CLIENTS_DB or not texte:
        return None
    import unicodedata
    def _n(s):
        return "".join(ch for ch in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(ch) != "Mn")

    texte_lower = _n(texte)
    mots = re.findall(r'[a-z0-9]+', texte_lower)
    tel_digits = re.sub(r'\D', '', texte)
    stop_mots = {"madame", "monsieur", "mme", "mr", "client", "maitre", "inconnu", "precise", "autre", "nouveau", "nouvelle"}

    # 1. Priorité absolue : Téléphone exact (>= 8 chiffres)
    if len(tel_digits) >= 8:
        for c in reversed(CLIENTS_DB):
            c_tels = "".join(c.get("tels_clean", [])) + re.sub(r'\D', '', c.get("tel", ""))
            if tel_digits in c_tels:
                return c

    # 2. Priorité 2 : Couple Maître + Animal ensemble (évite les homonymes comme Marco/Athena vs Serrere/Athena)
    for c in reversed(CLIENTS_DB):
        nom_a = _n(c.get("nom_animal", ""))
        nom_m = _n(c.get("nom_maitre", ""))
        parts_m = [p for p in re.findall(r'[a-z0-9]+', nom_m) if len(p) >= 4 and p not in stop_mots]
        if nom_a and len(nom_a) >= 3 and nom_a in mots and any(pm in mots for pm in parts_m):
            return c

    # 3. Priorité 3 : Maître seul (complet ou nom de famille)
    for c in reversed(CLIENTS_DB):
        nom_m = _n(c.get("nom_maitre", ""))
        if nom_m and len(nom_m) >= 4 and nom_m in texte_lower:
            return c
    for c in reversed(CLIENTS_DB):
        nom_m = _n(c.get("nom_maitre", ""))
        parts_m = [p for p in re.findall(r'[a-z0-9]+', nom_m) if len(p) >= 4 and p not in stop_mots]
        if any(pm in mots for pm in parts_m):
            return c

    # 4. Priorité 4 : Animal seul UNIQUEMENT si la phrase ne mentionne pas un autre maître explicite (ex: "Madame Serrere")
    if "madame " in texte_lower or "monsieur " in texte_lower or "mme " in texte_lower or "mr " in texte_lower:
        return None

    for c in reversed(CLIENTS_DB):
        nom_a = _n(c.get("nom_animal", ""))
        if nom_a and len(nom_a) >= 3 and nom_a in mots:
            return c

    return None

def trouver_clients_candidats(texte: str) -> list:
    """Retourne tous les clients candidats en cas d'homonyme (ex: plusieurs Nicolas ou plusieurs Athena)."""
    if not CLIENTS_DB or not texte:
        return []
    import unicodedata
    def _n(s):
        return "".join(ch for ch in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(ch) != "Mn")

    texte_lower = _n(texte)
    mots = re.findall(r'[a-z0-9]+', texte_lower)
    tel_digits = re.sub(r'\D', '', texte)
    stop_mots = {"madame", "monsieur", "mme", "mr", "client", "maitre", "inconnu", "precise", "autre", "nouveau", "nouvelle"}

    if len(tel_digits) >= 8:
        hits = [c for c in CLIENTS_DB if tel_digits in ("".join(c.get("tels_clean", [])) + re.sub(r'\D', '', c.get("tel", "")))]
        if hits:
            return hits

    hits_couple = []
    for c in CLIENTS_DB:
        nom_a = _n(c.get("nom_animal", ""))
        nom_m = _n(c.get("nom_maitre", ""))
        parts_m = [p for p in re.findall(r'[a-z0-9]+', nom_m) if len(p) >= 4 and p not in stop_mots]
        if nom_a and len(nom_a) >= 3 and nom_a in mots and any(pm in mots for pm in parts_m):
            hits_couple.append(c)
    if hits_couple:
        return hits_couple

    hits_maitre = []
    for c in CLIENTS_DB:
        nom_m = _n(c.get("nom_maitre", ""))
        parts_m = [p for p in re.findall(r'[a-z0-9]+', nom_m) if len(p) >= 4 and p not in stop_mots]
        if (nom_m and len(nom_m) >= 4 and nom_m in texte_lower) or any(pm in mots for pm in parts_m):
            hits_maitre.append(c)
    if hits_maitre:
        return hits_maitre

    return []

def deduire_secteur_par_date(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if dt.weekday() in [1, 2, 5]: # Mar, Mer, Sam
            return "Urrugne"
        elif dt.weekday() in [3, 4]: # Jeu, Ven
            return "Saint-Pée-sur-Nivelle"
    except Exception:
        pass
    return "Urrugne"

APPOINTMENTS_PATH = os.path.join(os.path.dirname(__file__), "appointments_to_sync.json")

def load_all_appointments():
    try:
        if os.path.exists(APPOINTMENTS_PATH):
            with open(APPOINTMENTS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log(f"[LOAD APPOINTMENTS ERROR] : {e}")
    return []

def save_all_appointments(appts):
    try:
        with open(APPOINTMENTS_PATH, "w", encoding="utf-8") as f:
            json.dump(appts, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"[SAVE APPOINTMENTS ERROR] : {e}")

JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]

def date_vers_texte_fr(dt_obj):
    j_nom = JOURS_FR[dt_obj.weekday()]
    m_nom = MOIS_FR[dt_obj.month]
    return f"{j_nom} {dt_obj.day} {m_nom} {dt_obj.year}"

def _duree_par_cat(cat: str) -> int:
    c = (cat or "B").upper()
    if c == "A": return 30
    if c == "C": return 90
    if c == "D": return 120
    return 60

def qualifier_categorie_animal(nom_animal: str, espece: str, race: str, telephone: str = "") -> tuple:
    nom_clean = (nom_animal or "").strip().lower()
    if nom_clean:
        overrides = charger_overrides_categories()
        import unicodedata
        nom_norm = "".join(ch for ch in unicodedata.normalize("NFD", nom_clean) if unicodedata.category(ch) != "Mn")
        if nom_norm in overrides:
            ov = overrides[nom_norm]
            cat = (ov.get("cat") or "B").upper()
            return cat, _duree_par_cat(cat), "Correction manuelle mémorisée (Google Sheet)"

    try:
        db_path = os.path.join(os.path.dirname(__file__), "clients_db.json")
        if os.path.exists(db_path):
            with open(db_path, "r", encoding="utf-8") as f_db:
                c_db = json.load(f_db)
            tel_clean = telephone.replace(" ", "").replace(".", "") if telephone else ""
            for item in c_db:
                item_nom = item.get("nom_animal", "").lower()
                if (nom_clean and nom_clean == item_nom) or (tel_clean and len(tel_clean) >= 8 and tel_clean in str(item).replace(" ", "")):
                    cat = (item.get("cat") or item.get("categorie") or "B").upper()
                    return cat, _duree_par_cat(cat), "Fiche client retrouvée en base"
    except Exception as e:
        log(f"[QUALIF ERROR] : {e}")

    if "chat" in (espece or "").lower():
        return "A", 30, "Chat (Cat. A - 30 min)"

    race_lower = (race or "").lower()

    # DICTIONNAIRE EXHAUSTIF DES GÉANTS & TRÈS GRANDS CHIENS (Cat. D - 2h00 strict)
    races_d = [
        "patou", "pyrénées", "pyrenees", "montagne des pyrénées", "montagne des pyrenees",
        "terre neuve", "terre-neuve", "leonberg", "léonberg", "saint bernard", "saint-bernard",
        "bouvier bernois", "briard", "bobtail", "dogue allemand", "dogue de bordeaux",
        "mastiff", "cane corso", "malamute", "barzoï", "lévrier irlandais", "irish wolfhound",
        "berger d'anatolie", "kangol", "berger du caucase", "akita"
    ]

    # DICTIONNAIRE EXHAUSTIF DES GRANDS CHIENS & POILS LOURDS (Cat. C - 1h30 strict)
    races_c = [
        "berger allemand poil long", "berger allemand poils longs",
        "labrador", "berger australien", "beauceron",
        "cocker", "épagneul", "epagneul", "samoyède", "samoyede", "husky",
        "chow", "chow-chow", "collie", "berger blanc suisse", "springer",
        "braque", "dalmatien", "boxer", "rottweiler", "berger picard", "weimaraner", "braque de weimar"
    ]

    # CHIENS CLASSÉS CATÉGORIE B (1h00) PAR RÈGLE MÉTIER SANDRINE
    races_b = [
        "golden", "golden retriever", "setter", "border collie",
        "berger allemand", "malinois", "maltais", "bichon maltais", "griffon"
    ]

    # PETITS CHIENS POIL RAS (Cat. A - 30 min)
    races_a = [
        "chihuahua", "pinscher", "teckel poil ras", "jack russel poil ras", "jack russell poil ras",
        "whippet", "petit lévrier italien", "boston terrier", "bouledogue français poil ras"
    ]

    for r in races_d:
        if r in race_lower:
            return "D", 120, f"Chien géant / lourd {race} (Cat. D - 2h00 strict)"
    # Vérifier d'abord les exceptions poils longs / griffons (Cat. C) avant Cat. B
    if "berger allemand" in race_lower and ("poil long" in race_lower or "poils longs" in race_lower):
        return "C", 90, f"Berger Allemand poils longs (Cat. C - 1h30 strict)"
    for r in races_b:
        if r in race_lower:
            return "B", 60, f"Race {race} classée en Cat. B (1h00)"
    for r in races_c:
        if r in race_lower:
            return "C", 90, f"Grand chien / poil fourni {race} (Cat. C - 1h30 strict)"
    for r in races_a:
        if r in race_lower:
            return "A", 30, f"Petit chien poil ras {race} (Cat. A - 30 min)"

    # SECOURS INTELLIGENT PAR L'IA : Si race inconnue ou orthographe approximative, Gemini tranche selon le gabarit
    if race_lower and race_lower not in ["non précisée", "inconnue", "chien", "race"]:
        try:
            url_g = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
            prompt_q = f"""Tu es expert toiletteur canin. Pour la race '{race}', classe son gabarit de toilettage :
A (30 min - très petit/poil ras)
B (60 min - petit/moyen standard comme caniche, bichon, yorkshire)
C (90 min - grand chien ou poil dense nécessitant gros séchage comme berger, golden)
D (120 min - chien géant comme patou, terre-neuve, léonberg, st-bernard).
Réponds STRICTEMENT par la lettre unique A, B, C ou D sans commentaire."""
            res = requests.post(url_g, json={"contents": [{"parts": [{"text": prompt_q}]}]}, timeout=3)
            if res.status_code == 200:
                cat_ia = res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip().upper()
                if cat_ia in ["A", "B", "C", "D"]:
                    dur_ia = {"A": 30, "B": 60, "C": 90, "D": 120}[cat_ia]
                    log(f"[QUALIF IA GEMINI] {race} classé en Catégorie {cat_ia} ({dur_ia} min)")
                    return cat_ia, dur_ia, f"Expertise IA pour {race} (Cat. {cat_ia} - {dur_ia} min)"
        except Exception:
            pass

    return "B", 60, f"Standard {race or 'Chien'} (Cat. B - 1h)"

def obtenir_evenements_google_calendar_direct() -> list:
    """Interroge Google Apps Script en temps réel pour obtenir la liste exacte de TOUS les événements dans l'agenda."""
    try:
        url = f"{APPS_SCRIPT_URL}?start=2026-11-24T00:00:00%2B01:00&end=2027-03-01T00:00:00%2B01:00"
        resp = requests.get(url, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                evs = data.get("events", [])
                log(f"[CALENDAR LIVE] {len(evs)} événements récupérés en temps réel depuis Google Agenda.")
                return evs
    except Exception as e:
        log(f"[CALENDAR LIVE FETCH NOTE] : {e}")
    return []

def trouver_premier_creneau_disponible(secteur: str, duree_minutes: int, cat: str = 'B') -> tuple:
    appts = load_all_appointments()
    
    # 1. Récupération en direct des événements réels de Google Agenda avec conversion fuseau horaire Europe/Paris (UTC+1)
    paris_tz = timezone(timedelta(hours=1))
    live_events = obtenir_evenements_google_calendar_direct()
    for lev in live_events:
        st_iso = lev.get("start", "")
        end_iso = lev.get("end", "")
        if st_iso:
            try:
                dt_s = datetime.fromisoformat(st_iso.replace("Z", "+00:00")).astimezone(paris_tz)
                dt_e = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).astimezone(paris_tz)
                d_part = dt_s.strftime("%Y-%m-%d")
                h_clean = dt_s.strftime("%H:%M")
                dur_calc = max(30, int((dt_e - dt_s).total_seconds() // 60))
                appts.append({
                    "date": d_part,
                    "time": f"{h_clean}:00",
                    "duration": dur_calc,
                    "title": lev.get("title", ""),
                    "description": f"Événement réel Google Agenda : {lev.get('title', '')}"
                })
            except Exception as e_parse:
                log(f"[PARSE ERR] : {e_parse}")

    valid_weekdays = [1, 2, 5] if "urrugne" in (secteur or "").lower() else [3, 4]

    # Début dès le retour de Sandrine : Mardi 24 Novembre 2026
    cur_date = datetime(2026, 11, 24)
    end_date = datetime(2027, 2, 28)

    while cur_date <= end_date:
        if cur_date.weekday() in valid_weekdays:
            # BLOCAGE DE SÉCURITÉ : Mardi 24 et Jeudi 26 Novembre fermés aux nouvelles réservations
            if cur_date.strftime("%Y-%m-%d") in ["2026-11-24", "2026-11-26"]:
                cur_date += timedelta(days=1)
                continue
            # BLOCAGE DE SÉCURITÉ : Mardi 24 et Jeudi 26 Novembre fermés aux nouvelles réservations
            if cur_date.strftime("%Y-%m-%d") in ["2026-11-24", "2026-11-26"]:
                cur_date += timedelta(days=1)
                continue
            date_str = cur_date.strftime("%Y-%m-%d")
            day_appts = [a for a in appts if a.get("date") == date_str]

            # RÈGLE 1 : Quotas journaliers stricts (Max 1 Cat D, Max 2 Cat C par jour)
            cat_d_count = sum(1 for a in day_appts if "cat. d" in a.get("description", "").lower() or a.get("duration", 0) >= 120)
            cat_c_count = sum(1 for a in day_appts if "cat. c" in a.get("description", "").lower() or a.get("duration", 0) == 90)

            if cat == "D" and cat_d_count >= 1:
                cur_date += timedelta(days=1)
                continue
            if cat == "C" and cat_c_count >= 2:
                cur_date += timedelta(days=1)
                continue

            # RÈGLE POSTURALE & SANTÉ SANDRINE (CATÉGORIE C - 1h30) :
            # Maximum 1 chien C le matin (09h-12h30) ET 1 chien C l'après-midi (14h-18h)
            cat_c_matin = 0
            cat_c_aprem = 0
            for a in day_appts:
                is_c = ("cat. c" in a.get("description", "").lower() or a.get("duration", 0) == 90)
                if is_c:
                    t_str = a.get("time", "09:00:00")[:2]
                    try:
                        h_val_c = int(t_str)
                        if h_val_c < 13:
                            cat_c_matin += 1
                        else:
                            cat_c_aprem += 1
                    except Exception:
                        pass

            occupied = []
            for a in day_appts:
                t_str = a.get("time", "09:00:00")[:5]
                try:
                    h, m = map(int, t_str.split(":"))
                    start_m = h * 60 + m
                    dur = a.get("duration", 60)
                    occupied.append((start_m, start_m + dur))
                except Exception:
                    pass
            occupied.sort()

            # Plages horaires strictes de travail de Sandrine au camion :
            # Matin : Premier départ à 09h00 (540 min), fin de matinée à 13h00 (780 min)
            # Pause déjeuner par défaut pour le site : 13h00 à 14h00 (Sandrine reste 100% libre de poser des rdv quand elle veut)
            # Après-midi : Début à 14h00 (840 min), FIN STRICTE DU DERNIER SOIN À 18h00 (1080 min)
            possible_starts = [540, 570, 600, 630, 660, 690, 720, 750, 840, 870, 900, 930, 960, 990, 1020, 1050]
            for s in possible_starts:
                e = s + duree_minutes
                
                # Respect des créneaux attribués aux clients web :
                # - Matin : de 09h00 à 13h00 (dernier soin du matin termine au plus tard à 13h00)
                # - Pause : 13h00 à 14h00 préservée pour le site
                # - Après-midi : de 14h00 à 18h00 STRICT (aucun soin ne peut finir après 18h00)
                is_matin = (s >= 540 and e <= 780)
                is_aprem = (s >= 840 and e <= 1080)
                if not (is_matin or is_aprem):
                    continue

                # RÈGLE CATÉGORIE C : Maximum 1 le matin et 1 l'après-midi
                if cat == "C":
                    if is_matin and cat_c_matin >= 1:
                        continue # Déjà un grand chien le matin -> on cherche l'après-midi
                    if is_aprem and cat_c_aprem >= 1:
                        continue # Déjà un grand chien l'après-midi -> jour suivant

                # RÈGLE ZÉRO CHEVAUCHEMENT : Vérification stricte avec l'agenda Google
                overlap = False
                for (occ_s, occ_e) in occupied:
                    if not (e <= occ_s or s >= occ_e):
                        overlap = True
                        break
                if overlap:
                    continue

                h_val = s // 60
                m_val = s % 60
                return date_str, f"{h_val:02d}:{m_val:02d}", cur_date

        cur_date += timedelta(days=1)
    return None, None, None

def inscrire_dans_google_calendar(p: dict) -> bool:
    try:
        date_str = p.get('date_proposee')
        heure_str = p.get('heure_debut', '09:00')
        duree = int(p.get('duree_minutes', 60))
        
        start_dt = datetime.strptime(f"{date_str} {heure_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duree)
        
        h_formatted = start_dt.strftime("%Hh%M")
        end_h_formatted = end_dt.strftime("%Hh%M")
        nom = p.get('nom_animal', 'Toilettage')
        maitre = p.get('nom_maitre', '').strip()
        race = p.get('race', '').strip()
        is_auto = p.get('is_auto', False) or p.get('recurrent', False)
        prefix_auto = "⏰ [AUTO] " if is_auto else ""

        # Formatage complet : Animal (Maître - Race)
        details_list = []
        if maitre and maitre not in ['Inconnu', 'Non précisé', 'Client']:
            details_list.append(maitre)
        if race and race not in ['race', 'Inconnue', 'Non précisée', 'Chien', 'Chat']:
            details_list.append(race)
        
        details_str = f" ({' - '.join(details_list)})" if details_list else ""
        title = f"{h_formatted} à {end_h_formatted}\n{prefix_auto}{nom}{details_str}"
        
        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:00+01:00")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:00+01:00")
        
        desc = f"DÉBUT DU SOIN : {heure_str} (Fin prévue : {end_dt.strftime('%H:%M')}) - Toilettage {nom} - Catégorie {p.get('categorie', 'B')} ({duree} min) - Secteur {p.get('secteur')}"
        if p.get('nom_maitre') and p.get('nom_maitre') != 'Inconnu':
            desc += f" - Maître : {p.get('nom_maitre')}"
        if p.get('race') and p.get('race') not in ['race', 'Inconnue', 'Non précisée']:
            desc += f" - Race : {p.get('race')}"
            
        payload = {
            "title": title,
            "start": start_iso,
            "end": end_iso,
            "description": desc,
            "location": "",
            "source": p.get("source", "web")
        }
        
        resp = requests.post(APPS_SCRIPT_URL, json=payload, timeout=20)
        if resp.status_code == 200:
            res_json = resp.json()
            if res_json.get("status") == "success":
                log(f"[CALENDAR OK] Rendez-vous Google Calendar inscrit : {res_json.get('eventId')}")
                return True
            elif res_json.get("status") == "conflict":
                log(f"[CALENDAR CONFLICT] Créneau refusé (déjà pris) : {res_json.get('message')}")
                return False
        log(f"[CALENDAR ERROR] {resp.status_code} {resp.text}")
    except Exception as e:
        log(f"[CALENDAR EXCEPTION] : {e}")
    return False

def traiter_instruction_avec_memoire(texte: str, historique: list, client: dict = None) -> dict:
    date_du_jour = get_date_du_jour_fr()
    calendrier_semaine = get_semaine_calendrier_fr()
    
    hist_text = ""
    if historique:
        hist_text = "HISTORIQUE RÉCENT DES ÉCHANGES DE LA CONVERSATION EN COURS :\n"
        for h in historique[-6:]:
            role = "Sandrine" if h.get("role") == "user" else "Assistant"
            hist_text += f"{role} : {h.get('text')}\n"
    
    infos_c = ""
    if client:
        infos_c = f"""FICHE DU CLIENT IDENTIFIÉ DANS LA BASE :
- Animal : {client.get('nom_animal')} | Maître : {client.get('nom_maitre')}
- Race : {client.get('race')} | Catégorie : {client.get('cat')} ({client.get('duree')})
- Secteur : {client.get('secteur')}
"""

    prompt = f"""Tu es l'assistant personnel de Sandrine Hiriart, toiletteuse canin/félin (La Canin Mobile / Canins Toilettage).
Sandrine te parle au micro depuis son camion ou par texte.

DATE EXACTE DU JOUR : **{date_du_jour}**.
CALENDRIER EXACT DE LA SEMAINE COURANTE :
{calendrier_semaine}

RÈGLES D'OR DE COMPORTEMENT (INVIOLABLES) :
1. POSTURE : SANDRINE EST LA PATRONNE ABSOLUE. Tu es là pour lui faciliter la vie, lui obéir immédiatement et noter ce qu'elle demande.
2. ZÉRO MORALE, ZÉRO POLÉMIQUE, ZÉRO LEÇON : Ne lui dis JAMAIS "attention c'est ta pause déjeuner" ou "ce n'est pas permis". Si elle te demande de poser un rdv à 13h30 ou n'importe quand (pour des tests ou des exceptions), TU OBÉIS IMMÉDIATEMENT sans discuter et sans poser 50 questions !
3. MÉMOIRE DU DIALOGUE : Utilise impérativement l'historique récent ci-dessous. Si elle a parlé d'un animal (ex: Eden) juste avant, tu sais que sa demande concerne cet animal sans avoir à lui redemander son nom ou sa race !
4. CONCISION ET CHALEUR : Sois courtois, concis, direct et efficace.

{infos_c}
{hist_text}

PLANNING MASTER DE RÉFÉRENCE DE SANDRINE :
\"\"\"
{AGENDA_TEXT}
\"\"\"

Dernière phrase de Sandrine : "{texte}"

Tu dois analyser sa demande en tenant compte de tout le contexte ci-dessus et répondre UNIQUEMENT par ce JSON strict :

1. Si elle demande de réserver, noter, inscrire, prendre, créer ou bloquer un NOUVEAU créneau / rendez-vous (toute demande contenant une date ou une heure pour prendre/créer/bloquer un rendez-vous, ex: "Créer rdv...", "Prendre créneau...", "Rdv Savanah...", "réserve pour mercredi à 13h30", "ajoute Pearl", "mets Eden...") :
RÈGLE STRICTE : Toute demande contenant une date/heure pour prendre, créer ou bloquer un rendez-vous est TOUJOURS "INSCRIRE", JAMAIS "CREER_CLIENT" (réservé uniquement à la création d'une fiche client sans rendez-vous) et JAMAIS "DEPLACER" (sauf s'il y a explicitement un mot de déplacement/report comme "déplace", "décale", "reporte").
{{
  "action": "INSCRIRE",
  "nom_animal": "nom de l'animal déduit de la phrase ou de l'historique",
  "nom_maitre": "nom du maître ou Inconnu",
  "race": "race de l'animal",
  "categorie": "A" ou "B" ou "C" ou "D",
  "duree_minutes": 30 ou 60 ou 90 ou 120,
  "date_proposee": "YYYY-MM-DD",
  "heure_debut": "HH:MM",
  "secteur": "Urrugne" ou "Saint-Pée-sur-Nivelle",
  "message_reponse": "Phrase courte et chaleureuse confirmant l'inscription"
}}

2. Si elle demande explicitement de DÉPLACER ou REPORTER un rendez-vous déjà existant (ex: "déplace Farah du 28 novembre à 16h", "décale Alba à demain 10h") :
{{
  "action": "DEPLACER",
  "nom_animal": "nom de l'animal",
  "nom_maitre": "nom du maître si précisé sinon vide (optionnel)",
  "nouveau_nom_animal": "nouveau nom de l'animal si précisé sinon vide (optionnel)",
  "old_date": "YYYY-MM-DD",
  "new_date": "YYYY-MM-DD",
  "new_heure": "HH:MM",
  "duree_minutes": 60,
  "message_reponse": "Phrase courte résumant le déplacement proposé"
}}

3. Si elle demande d'ANNULER ou SUPPRIMER un rendez-vous déjà existant (ex: "annule Phara le 24 novembre", "supprime le rdv de Brioche") :
{{
  "action": "ANNULER",
  "nom_animal": "nom de l'animal",
  "nom_maitre": "nom du maître si précisé sinon vide (optionnel)",
  "nouveau_nom_animal": "nouveau nom de l'animal si précisé sinon vide (optionnel)",
  "date": "YYYY-MM-DD",
  "message_reponse": "Phrase courte résumant l'annulation proposée"
}}

4. Si elle demande de CORRIGER LA CATÉGORIE d'un animal (ex: "mets Alba en catégorie D", "corrige la catégorie de Phara en C", "Taïko est un cat C") :
{{
  "action": "CORRIGER_CATEGORIE",
  "nom_animal": "nom de l'animal",
  "categorie": "A" ou "B" ou "C" ou "D",
  "race": "race si précisée sinon vide",
  "nom_maitre": "nom du maître si précisé sinon vide",
  "message_reponse": "Phrase courte résumant la correction de catégorie"
}}

5. Si elle demande UNIQUEMENT de CRÉER UN NOUVEAU CLIENT / UNE NOUVELLE FICHE SANS RENDEZ-VOUS / SANS DATE NI HEURE (ex: "nouveau client Madame Serrere, Athena, Bichon Frisé, cat B, 06...", "crée la fiche de...") :
{{
  "action": "CREER_CLIENT",
  "nom_maitre": "nom complet du maître",
  "nom_animal": "nom de l'animal",
  "espece": "Chien" ou "Chat",
  "race": "race exacte",
  "categorie": "A" ou "B" ou "C" ou "D",
  "telephone": "numéro de téléphone ou vide",
  "secteur": "Urrugne" ou "Saint-Pée-sur-Nivelle",
  "message_reponse": "Phrase courte résumant la fiche à créer"
}}

6. Si elle pose une question (créneaux libres, horaires, infos client...) :
RÈGLE ABSOLUE : Dans REPONDRE, n'affirme JAMAIS avoir modifié, annulé, déplacé, créé ou corrigé quoi que ce soit.
{{
  "action": "REPONDRE",
  "message_reponse": "Réponse claire, précise et directe sans moraliser ni surcharger d'explications superflues"
}}
"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=35)
        if resp.status_code == 200:
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
            else:
                clean = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
                data = json.loads(clean)
                
            if data.get("action") == "INSCRIRE":
                if client:
                    if not data.get("nom_animal") or data.get("nom_animal") in ["nom de l'animal", "Animal"]:
                        data["nom_animal"] = client.get("nom_animal")
                    if not data.get("nom_maitre") or data.get("nom_maitre") == "Inconnu":
                        data["nom_maitre"] = client.get("nom_maitre")
                    if not data.get("race") or data.get("race") in ["race", "Non précisée", "Inconnue", ""]:
                        data["race"] = client.get("race", data.get("race"))
                    if not data.get("categorie"):
                        data["categorie"] = client.get("cat", "B")
                    cat_u = data.get("categorie", "B").upper()
                    if cat_u == "A": data["duree_minutes"] = 30
                    elif cat_u == "C": data["duree_minutes"] = 90
                    elif cat_u == "D": data["duree_minutes"] = 120
                    else: data["duree_minutes"] = 60
                    
                if data.get("date_proposee"):
                    data["secteur"] = deduire_secteur_par_date(data["date_proposee"])
                    
            return data
    except Exception as e:
        log(f"[ANALYSE ERROR] : {e}")
    return None

def process_message(msg):
    chat_id = str(msg["chat"]["id"])
    date_du_jour = get_date_du_jour_fr()
    
    with TelegramTypingAction(chat_id, "typing"):
        if chat_id not in USER_STATES:
            USER_STATES[chat_id] = {"historique": [], "last_greeted_date": ""}
            
        if "historique" not in USER_STATES[chat_id]:
            USER_STATES[chat_id]["historique"] = []
            
        texte = ""
        is_voice = False
        if "text" in msg:
            texte = msg["text"].strip()
        elif "voice" in msg:
            is_voice = True
            texte = transcrire_audio(msg["voice"]["file_id"])
            if not texte:
                send_telegram_message(chat_id, "❌ Désolé Sandrine, je n'ai pas pu entendre. Peux-tu me le réenregistrer ?")
                return
            
        if not texte: return

        if texte.lower() in ["/reset", "reset", "/clear"]:
            USER_STATES[chat_id]["historique"] = []
            for aid in [k for k, v in PENDING_ACTIONS.items() if v.get("chat_id") == chat_id]:
                PENDING_ACTIONS.pop(aid, None)
            save_states()
            send_telegram_message(chat_id, "🔄 Mémoire de la conversation remise à zéro ! Que souhaites-tu faire ?")
            return
        
        # Salutation UNIQUEMENT si c'est la toute première interaction du jour
        salutation = ""
        if USER_STATES[chat_id].get("last_greeted_date") != date_du_jour:
            salutation = f"Bonjour Sandrine, nous sommes le **{date_du_jour}**. Que puis-je faire pour toi ? Je suis prêt à prendre des rendez-vous.\n\n"
            USER_STATES[chat_id]["last_greeted_date"] = date_du_jour
            save_states()
            
        # Détection client dans le texte courant ou dans l'historique récent
        client_rec = trouver_client_dans_base(texte)
        if not client_rec:
            for h in reversed(USER_STATES[chat_id]["historique"][-4:]):
                client_rec = trouver_client_dans_base(h.get("text", ""))
                if client_rec: break
                
        analyse = traiter_instruction_avec_memoire(texte, USER_STATES[chat_id]["historique"], client_rec)
        
        # Historique de dialogue
        USER_STATES[chat_id]["historique"].append({"role": "user", "text": texte})
        if len(USER_STATES[chat_id]["historique"]) > 12:
            USER_STATES[chat_id]["historique"] = USER_STATES[chat_id]["historique"][-12:]
        save_states()
        
        # Préfixe si message vocal pour afficher ce qui a été compris
        vocal_tag = f"🎙️ _« {texte} »_\n\n" if is_voice else ""
        
        if not analyse:
            rep_fail = "Je n'ai pas bien saisi Sandrine. Peux-tu me redire ce que tu souhaites ?"
            USER_STATES[chat_id]["historique"].append({"role": "assistant", "text": rep_fail})
            save_states()
            send_telegram_message(chat_id, f"{salutation}{vocal_tag}🤔 {rep_fail}")
            return
            
        action = analyse.get("action", "REPONDRE")
        
        # 1. Action : Répondre à une question
        if action == "REPONDRE":
            rep = analyse.get("message_reponse", "Voici ce que j'ai trouvé pour toi.")
            USER_STATES[chat_id]["historique"].append({"role": "assistant", "text": rep})
            save_states()
            send_telegram_message(chat_id, f"{salutation}{vocal_tag}💬 {rep}")
            return
            
        # 2. Action : Inscrire directement dans l'agenda
        if action == "INSCRIRE":
            candidats = trouver_clients_candidats(texte)
            if len(candidats) > 1:
                liste_txt = "\n".join(
                    f"• *{c.get('nom_animal')}* ({c.get('race', '?')} - Cat. {c.get('cat', 'B')}) — *{c.get('nom_maitre')}* (`{c.get('tel') or 'sans tél'}`)"
                    for c in candidats[:5]
                )
                msg_ambig = (
                    f"{salutation}{vocal_tag}⚠️ *Plusieurs fiches correspondent dans la base :*\n\n"
                    f"{liste_txt}\n\n"
                    f"👉 *Lequel est-ce ?* Précise-moi le **nom de famille**, le **prénom de l'animal** ou le **numéro de téléphone** avant que j'inscrive."
                )
                USER_STATES[chat_id]["historique"].append({"role": "assistant", "text": msg_ambig})
                save_states()
                send_telegram_message(chat_id, msg_ambig)
                return

            animal = analyse.get("nom_animal", "Toilettage")
            race = analyse.get("race", "")
            cat = analyse.get("categorie", "B")
            dur = analyse.get("duree_minutes", 60)
            date_p = analyse.get("date_proposee", "")
            h = analyse.get("heure_debut", "09:00")
            sec = analyse.get("secteur", "Urrugne")
            maitre = analyse.get("nom_maitre", "")
            rep_phrase = analyse.get("message_reponse", "C'est noté.")
            
            send_telegram_message(chat_id, "📅 _Inscription immédiate dans ton agenda Google CANINS MOBILE..._")
            analyse["source"] = "telegram"
            succes = inscrire_dans_google_calendar(analyse)
            status_agenda = "🎉 *👉 Inscrit automatiquement dans ton Google Agenda CANINS MOBILE !*" if succes else "⚠️ *Enregistré localement, synchronisation en attente.*"
            
            maitre_str = f"\n👤 *Client* : {maitre}" if maitre and maitre != "Inconnu" else ""
            race_str = f" ({race})" if race and race not in ["race", "Inconnue", "Non précisée"] else ""
            
            msg_conf = f"""{salutation}{vocal_tag}✅ *RENDEZ-VOUS ENREGISTRÉ DANS TON AGENDA !*

🐾 *Animal* : *{animal}*{race_str}{maitre_str}
⏱️ *Catégorie* : `Cat. {cat}` ({dur} min)
📅 *Date* : *{date_p}*
⏰ *Heure* : *{h}*
🚐 *Secteur* : *{sec}*

💬 « _{rep_phrase}_ »

{status_agenda}"""
            
            USER_STATES[chat_id]["historique"].append({"role": "assistant", "text": f"Rendez-vous inscrit pour {animal} le {date_p} à {h}"})
            save_states()
            send_telegram_message(chat_id, msg_conf)
            return

        # 3. Actions critiques avec bouton de confirmation 1-clic (DEPLACER / ANNULER / CORRIGER_CATEGORIE / CREER_CLIENT)
        if action in ["DEPLACER", "ANNULER", "CORRIGER_CATEGORIE", "CREER_CLIENT"]:
            import uuid
            act_id = uuid.uuid4().hex[:10]
            PENDING_ACTIONS[act_id] = {"chat_id": chat_id, "analyse": analyse}

            if action == "DEPLACER":
                resume = (
                    f"🔄 *CONFIRMATION DE DÉPLACEMENT*\n\n"
                    f"🐾 *Animal* : *{analyse.get('nom_animal', '?')}*\n"
                    f"📅 *Ancienne date* : `{analyse.get('old_date', '?')}`\n"
                    f"➡️ *Nouveau créneau* : *{analyse.get('new_date', '?')}* à *{analyse.get('new_heure', '?')}*\n\n"
                    f"Je fais ce changement dans Google Agenda ?"
                )
            elif action == "ANNULER":
                resume = (
                    f"🗑️ *CONFIRMATION D'ANNULATION*\n\n"
                    f"🐾 *Animal* : *{analyse.get('nom_animal', '?')}*\n"
                    f"📅 *Date* : `{analyse.get('date', '?')}`\n\n"
                    f"Je supprime ce rendez-vous de Google Agenda ?"
                )
            elif action == "CREER_CLIENT":
                cat_c = (analyse.get("categorie") or "B").upper()
                resume = (
                    f"👤 *CRÉATION DE FICHE CLIENT*\n\n"
                    f"👤 *Maître* : *{analyse.get('nom_maitre', '?')}*\n"
                    f"🐾 *Animal* : *{analyse.get('nom_animal', '?')}* ({analyse.get('race', 'Non précisée')})\n"
                    f"⏱️ *Catégorie* : `Cat. {cat_c}` ({_duree_par_cat(cat_c)} min)\n"
                    f"📞 *Téléphone* : `{analyse.get('telephone', 'Non précisé')}`\n"
                    f"📍 *Secteur* : *{analyse.get('secteur', 'Urrugne')}*\n\n"
                    f"J'enregistre ce client dans la base et le Google Sheet ?"
                )
            else:
                cat_new = (analyse.get("categorie") or "B").upper()
                resume = (
                    f"🏷️ *CONFIRMATION DE CATÉGORIE*\n\n"
                    f"🐾 *Animal* : *{analyse.get('nom_animal', '?')}*\n"
                    f"⏱️ *Nouvelle catégorie* : `Cat. {cat_new}` ({_duree_par_cat(cat_new)} min)\n\n"
                    f"J'enregistre cette catégorie définitivement dans la base ?"
                )

            kb = {
                "inline_keyboard": [[
                    {"text": "✅ Oui, confirmer", "callback_data": f"confirm:{act_id}"},
                    {"text": "❌ Non, annuler", "callback_data": f"cancel:{act_id}"}
                ]]
            }
            short_hist = {
                "DEPLACER": f"Proposition de déplacement pour {analyse.get('nom_animal', '?')} ({analyse.get('old_date', '?')} -> {analyse.get('new_date', '?')} à {analyse.get('new_heure', '?')}) en attente de confirmation.",
                "ANNULER": f"Proposition d'annulation pour {analyse.get('nom_animal', '?')} le {analyse.get('date', '?')} en attente de confirmation.",
                "CORRIGER_CATEGORIE": f"Proposition de correction de catégorie pour {analyse.get('nom_animal', '?')} -> Cat. {(analyse.get('categorie') or 'B').upper()} en attente de confirmation.",
                "CREER_CLIENT": f"Proposition de création de fiche client pour {analyse.get('nom_maitre', '?')} / {analyse.get('nom_animal', '?')} en attente de confirmation.",
            }
            _append_history(chat_id, "assistant", short_hist[action])
            send_telegram_message(chat_id, f"{salutation}{vocal_tag}{resume}", reply_markup=kb)
            return


def obtenir_tous_les_creneaux_disponibles(secteur: str, duree_minutes: int, cat: str = 'B', max_jours: int = 14) -> list:
    """Calcule tous les créneaux réels disponibles par jour pour l'affichage calendrier."""
    appts = load_all_appointments()
    paris_tz = timezone(timedelta(hours=1))
    live_events = obtenir_evenements_google_calendar_direct()
    for lev in live_events:
        st_iso = lev.get("start", "")
        end_iso = lev.get("end", "")
        if st_iso:
            try:
                dt_s = datetime.fromisoformat(st_iso.replace("Z", "+00:00")).astimezone(paris_tz)
                dt_e = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).astimezone(paris_tz)
                dur_calc = max(30, int((dt_e - dt_s).total_seconds() // 60))
                appts.append({
                    "date": dt_s.strftime("%Y-%m-%d"),
                    "time": dt_s.strftime("%H:%M:00"),
                    "duration": dur_calc,
                    "title": lev.get("title", ""),
                    "description": f"Événement réel Google Agenda : {lev.get('title', '')}"
                })
            except Exception:
                pass

    valid_weekdays = [1, 2, 5] if "urrugne" in (secteur or "").lower() else [3, 4]
    cur_date = datetime(2026, 11, 24)
    end_date = datetime(2027, 2, 28)

    resultats = []
    jours_courts = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]
    mois_courts = ["", "Janv.", "Févr.", "Mars", "Avr.", "Mai", "Juin", "Juil.", "Août", "Sept.", "Oct.", "Nov.", "Déc."]

    while cur_date <= end_date and len(resultats) < max_jours:
        if cur_date.weekday() in valid_weekdays:
            # BLOCAGE DE SÉCURITÉ : Mardi 24 et Jeudi 26 Novembre fermés aux nouvelles réservations
            if cur_date.strftime("%Y-%m-%d") in ["2026-11-24", "2026-11-26"]:
                cur_date += timedelta(days=1)
                continue
            # BLOCAGE DE SÉCURITÉ : Mardi 24 et Jeudi 26 Novembre fermés aux nouvelles réservations
            if cur_date.strftime("%Y-%m-%d") in ["2026-11-24", "2026-11-26"]:
                cur_date += timedelta(days=1)
                continue
            date_str = cur_date.strftime("%Y-%m-%d")
            day_appts = [a for a in appts if a.get("date") == date_str]

            # Quotas journaliers
            cat_d_count = sum(1 for a in day_appts if "cat. d" in a.get("description", "").lower() or a.get("duration", 0) >= 120)
            cat_c_count = sum(1 for a in day_appts if "cat. c" in a.get("description", "").lower() or a.get("duration", 0) == 90)

            if not (cat == "D" and cat_d_count >= 1) and not (cat == "C" and cat_c_count >= 2):
                cat_c_matin = sum(1 for a in day_appts if ("cat. c" in a.get("description", "").lower() or a.get("duration", 0) == 90) and int(a.get("time", "09:00:00")[:2]) < 13)
                cat_c_aprem = sum(1 for a in day_appts if ("cat. c" in a.get("description", "").lower() or a.get("duration", 0) == 90) and int(a.get("time", "09:00:00")[:2]) >= 13)

                occupied = []
                for a in day_appts:
                    try:
                        h, m = map(int, a.get("time", "09:00:00")[:5].split(":"))
                        dur = a.get("duration", 60)
                        occupied.append((h * 60 + m, h * 60 + m + dur))
                    except Exception:
                        pass
                occupied.sort()

                creneaux_jour = []
                for s in [540, 570, 600, 630, 660, 690, 720, 750, 840, 870, 900, 930, 960, 990, 1020, 1050]:
                    e = s + duree_minutes
                    is_matin = (s >= 540 and e <= 780)
                    is_aprem = (s >= 840 and e <= 1080)
                    if not (is_matin or is_aprem):
                        continue
                    if cat == "C":
                        if is_matin and cat_c_matin >= 1:
                            continue
                        if is_aprem and cat_c_aprem >= 1:
                            continue

                    overlap = any(not (e <= occ_s or s >= occ_e) for occ_s, occ_e in occupied)
                    if not overlap:
                        h_val = s // 60
                        m_val = s % 60
                        periode = "Matin" if is_matin else "Après-midi"
                        creneaux_jour.append({
                            "heure": f"{h_val:02d}:{m_val:02d}",
                            "periode": periode
                        })

                if creneaux_jour:
                    j_court = jours_courts[cur_date.weekday()]
                    m_court = mois_courts[cur_date.month]
                    badge_date = f"{j_court} {cur_date.day} {m_court}"
                    resultats.append({
                        "date": date_str,
                        "badge_date": badge_date,
                        "date_fr": date_vers_texte_fr(cur_date),
                        "creneaux": creneaux_jour
                    })

        cur_date += timedelta(days=1)
    return resultats


class WebhookHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/logs":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            log_text = "\n".join(LOG_BUFFER) if LOG_BUFFER else "No logs yet recorded."
            self.wfile.write(log_text.encode("utf-8"))
            return

        # Affichage de la page web mobile de réservation en ligne
        if self.path in ["/", "/reserver", "/reservation"]:
            if os.path.exists(RESERVER_HTML_PATH):
                try:
                    with open(RESERVER_HTML_PATH, "r", encoding="utf-8") as f:
                        html_content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(html_content.encode("utf-8"))
                    return
                except Exception as e_html:
                    log(f"[HTML SERVE ERROR] : {e_html}")

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("🚐 La Canin Mobile Telegram Webhook (Gemini 2.5 Flash Direct Locked) is LIVE and HEALTHY! 🐶✨".encode("utf-8"))

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length)

            # Route API : Liste des créneaux pour le calendrier de réservation
            if self.path == "/api/creneaux_disponibles":
                try:
                    payload = json.loads(post_data.decode("utf-8"))
                    sec = payload.get("secteur", "Urrugne")
                    nom_a = payload.get("nom_animal", "Animal")
                    esp = payload.get("espece", "Chien")
                    race = payload.get("race", "Non précisée")

                    cat, dur, _ = qualifier_categorie_animal(nom_a, esp, race)
                    dispos = obtenir_tous_les_creneaux_disponibles(sec, dur, cat, max_jours=10)

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success", "categorie": cat, "duree": dur, "jours": dispos}, ensure_ascii=False).encode("utf-8"))
                    return
                except Exception as e_cr:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": str(e_cr)}).encode("utf-8"))
                    return

            # 1. Route API de réservation web directe (Attribution ferme et directe dans l'agenda)
            if self.path == "/api/reserver":
                try:
                    payload = json.loads(post_data.decode("utf-8"))
                    nom_a = payload.get("nom_animal", "Animal").strip()
                    nom_m = payload.get("nom_maitre", "Client").strip()
                    tel = payload.get("telephone", "").strip()
                    sec = payload.get("secteur", "Urrugne").strip()
                    esp = payload.get("espece", "Chien").strip()
                    race = payload.get("race", "Non précisée").strip()

                    log(f"[DEMANDE WEB FERME] {nom_a} ({nom_m} - {tel}) à {sec}")

                    # 1. Qualification automatique de la catégorie
                    cat, dur, cat_source = qualifier_categorie_animal(nom_a, esp, race, tel)

                    # 2. Choix du client ou premier créneau disponible
                    date_choisie = payload.get("date_choisie", "").strip()
                    heure_choisie = payload.get("heure_choisie", "").strip()

                    if date_choisie and heure_choisie:
                        date_str = date_choisie
                        heure_str = heure_choisie
                        try:
                            dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
                        except Exception:
                            dt_obj = datetime.now()
                    else:
                        date_str, heure_str, dt_obj = trouver_premier_creneau_disponible(sec, dur, cat)
                        if not date_str:
                            date_str = "2026-11-24" if "urrugne" in sec.lower() else "2026-11-26"
                            heure_str = "09:00"
                            dt_obj = datetime.strptime(date_str, "%Y-%m-%d")

                    date_fr_label = date_vers_texte_fr(dt_obj)
                    lieu_exact = "Maison des Associations (Kixoenea)" if "urrugne" in sec.lower() else "À côté du Garage Renault"

                    # 2bis. Vérification anti-doublon par animal / téléphone dans Google Agenda
                    doublon_decision = payload.get("doublon_decision", "").strip()
                    if not doublon_decision:
                        import unicodedata
                        def _norm(s):
                            return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")

                        nom_norm = _norm(nom_a).strip()
                        tel_digits = re.sub(r"\D", "", tel)
                        paris_tz = timezone(timedelta(hours=1))
                        today_d = datetime.now(paris_tz).date()
                        live_evs = obtenir_evenements_google_calendar_direct()

                        for lev in live_evs:
                            st_iso = lev.get("start", "")
                            if not st_iso:
                                continue
                            try:
                                dt_ev = datetime.fromisoformat(st_iso.replace("Z", "+00:00")).astimezone(paris_tz)
                            except Exception:
                                continue
                            if dt_ev.date() < today_d:
                                continue

                            ev_title_norm = _norm(lev.get("title", ""))
                            ev_desc_digits = re.sub(r"\D", "", lev.get("description", ""))
                            match_tel = len(tel_digits) >= 8 and tel_digits in ev_desc_digits
                            match_nom = (
                                len(nom_norm) >= 2
                                and nom_norm not in ["animal", "chien", "chat", "toilettage", "non precise"]
                                and bool(re.search(r"\b" + re.escape(nom_norm) + r"\b", ev_title_norm))
                            )
                            if match_tel or match_nom:
                                ev_date_str = dt_ev.strftime("%Y-%m-%d")
                                ev_heure_str = dt_ev.strftime("%Hh%M")
                                ev_date_fr = date_vers_texte_fr(dt_ev)
                                status_code_res = "conflict_same_day" if ev_date_str == date_str else "conflict_other_day"
                                resp_conflict = {
                                    "status": status_code_res,
                                    "existing_event_id": lev.get("id", ""),
                                    "existing_date": ev_date_str,
                                    "existing_date_fr": ev_date_fr,
                                    "existing_heure": ev_heure_str,
                                    "new_date_fr": date_fr_label,
                                    "new_heure": heure_str.replace(":", "h"),
                                    "nom_animal": nom_a
                                }
                                self.send_response(200)
                                self.send_header("Content-Type", "application/json; charset=utf-8")
                                self.end_headers()
                                self.wfile.write(json.dumps(resp_conflict, ensure_ascii=False).encode("utf-8"))
                                return

                    # 2ter. Si le client a choisi de DÉPLACER son rendez-vous existant vers le nouveau créneau
                    if doublon_decision == "replace":
                        start_dt_new = datetime.strptime(f"{date_str} {heure_str}", "%Y-%m-%d %H:%M")
                        end_dt_new = start_dt_new + timedelta(minutes=dur)
                        h_f = start_dt_new.strftime("%Hh%M")
                        end_h_f = end_dt_new.strftime("%Hh%M")
                        det = [x for x in [nom_m if nom_m not in ["Client", "Inconnu", "Non précisé"] else "", race if race not in ["Non précisée", "Chien", "Chat", ""] else ""] if x]
                        det_str = f" ({' - '.join(det)})" if det else ""
                        new_title_str = f"{h_f} à {end_h_f}\n{nom_a}{det_str}"

                        move_payload = {
                            "action": "move",
                            "eventId": payload.get("existing_event_id", ""),
                            "nom_animal": nom_a,
                            "telephone": tel,
                            "old_date": payload.get("existing_date", ""),
                            "new_start": start_dt_new.strftime("%Y-%m-%dT%H:%M:00+01:00"),
                            "new_end": end_dt_new.strftime("%Y-%m-%dT%H:%M:00+01:00"),
                            "new_title": new_title_str
                        }
                        r_mv = requests.post(APPS_SCRIPT_URL, json=move_payload, timeout=20).json()
                        if r_mv.get("status") == "success":
                            alert_mv = (
                                f"🔄 *RENDEZ-VOUS DÉPLACÉ PAR LE CLIENT*\n\n"
                                f"🐾 *Animal* : *{nom_a}* ({esp} - {race})\n"
                                f"📅 *Nouveau créneau* : *{date_fr_label}* à *{heure_str.replace(':', 'h')}*\n"
                                f"📍 *Lieu fixe* : *{lieu_exact}* ({sec})\n"
                                f"👤 *Propriétaire* : *{nom_m}*\n"
                                f"📞 *Téléphone* : `{tel}`\n\n"
                                f"✅ *Mis à jour automatiquement dans Google Agenda CANINS MOBILE !*"
                            )
                            for cid in ["6022976635", "8771111289"]:
                                send_telegram_message(cid, alert_mv)
                            sms_mv = (
                                f"Bonjour ! Votre RDV pour {nom_a} a bien été déplacé au {date_fr_label} à {heure_str.replace(':', 'h')} "
                                f"({lieu_exact}, {sec}). À très vite ! Sandrine - La Canin Mobile"
                            )
                            threading.Thread(target=envoyer_sms_macrodroid, args=(tel, sms_mv), daemon=True).start()
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "status": "success",
                                "date_confirmee": date_fr_label,
                                "heure_confirmee": heure_str.replace(":", "h"),
                                "lieu": lieu_exact,
                                "secteur": sec,
                                "nom_animal": nom_a,
                                "nom_maitre": nom_m
                            }, ensure_ascii=False).encode("utf-8"))
                            return
                        else:
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json; charset=utf-8")
                            self.end_headers()
                            self.wfile.write(json.dumps({"status": "error", "message": r_mv.get("message", "Impossible de déplacer le rendez-vous.")}, ensure_ascii=False).encode("utf-8"))
                            return

                    # 3. Préparation pour Google Calendar
                    booking_payload = {
                        "nom_animal": nom_a,
                        "nom_maitre": nom_m,
                        "telephone": tel,
                        "secteur": sec,
                        "date_proposee": date_str,
                        "heure_debut": heure_str,
                        "duree_minutes": dur,
                        "categorie": cat,
                        "race": race,
                        "espece": esp,
                        "location": lieu_exact
                    }

                    # Inscription en direct dans Google Calendar
                    succes = inscrire_dans_google_calendar(booking_payload)
                    if succes and tel:
                        sms_conf = (
                            f"Bonjour ! Votre RDV pour {nom_a} est confirmé le {date_fr_label} à {heure_str.replace(':', 'h')} "
                            f"({lieu_exact}, {sec}). Pensez à prévenir 24h avant en cas d'empêchement. Sandrine - La Canin Mobile"
                        )
                        threading.Thread(target=envoyer_sms_macrodroid, args=(tel, sms_conf), daemon=True).start()

                    # 4. Enregistrement local pour verrouiller le créneau immédiatement
                    appts = load_all_appointments()
                    appts.append({
                        "date": date_str,
                        "time": f"{heure_str}:00",
                        "duration": dur,
                        "title": f"{nom_a} ({nom_m})",
                        "description": f"Toilettage {nom_a} - Cat. {cat} ({dur} min) - Tél: {tel} - Maître: {nom_m} - Secteur: {sec}",
                        "location": f"{sec} - {lieu_exact}"
                    })
                    save_all_appointments(appts)

                    # 5. Notification Telegram instantanée à Sandrine & Bernadette
                    alert = f"""✅ *NOUVEAU RENDEZ-VOUS FERME ATTRIBUÉ & CONFIRMÉ* !

🐾 *Animal* : *{nom_a}* ({esp} - {race})
⏱️ *Catégorie* : `Cat. {cat}` ({dur} min) — _{cat_source}_
📅 *Date* : *{date_fr_label}* à *{heure_str.replace(':', 'h')}*
📍 *Lieu fixe* : *{lieu_exact}* ({sec})
👤 *Propriétaire* : *{nom_m}*
📞 *Téléphone* : `{tel}`

{"🎉 👉 *Inscrit automatiquement dans ton Google Agenda CANINS MOBILE !*" if succes else "⚠️ *Enregistré dans le planning, synchronisation agenda en attente.*"}"""

                    for cid in ["6022976635", "8771111289"]:
                        send_telegram_message(cid, alert)

                    response_data = {
                        "status": "success",
                        "date_confirmee": date_fr_label,
                        "heure_confirmee": heure_str.replace(":", "h"),
                        "lieu": lieu_exact,
                        "secteur": sec,
                        "nom_animal": nom_a,
                        "nom_maitre": nom_m
                    }

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(response_data).encode("utf-8"))
                    return
                except Exception as e_res:
                    log(f"[API RÉSERVATION ERROR] : {e_res}")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": str(e_res)}).encode("utf-8"))
                    return

            # 2. Route API d'annulation par courtoisie
            if self.path == "/api/annuler":
                try:
                    payload = json.loads(post_data.decode("utf-8"))
                    tel = payload.get("telephone", "").replace(" ", "").replace(".", "")
                    nom_a_raw = payload.get("nom_animal", "").strip()
                    nom_a = nom_a_raw.lower()

                    # 1. Suppression RÉELLE et directe dans Google Calendar via Apps Script
                    r_del_json = {}
                    try:
                        del_payload = {
                            "action": "delete",
                            "nom_animal": nom_a,
                            "telephone": tel
                        }
                        r_del = requests.post(APPS_SCRIPT_URL, json=del_payload, timeout=15)
                        log(f"[SUPPRESSION GOOGLE CALENDAR] Réponse : {r_del.text}")
                        if r_del.status_code == 200:
                            r_del_json = r_del.json()
                    except Exception as e_del_gcal:
                        log(f"[SUPPRESSION GCAL ERROR] : {e_del_gcal}")

                    gcal_deleted = r_del_json.get("status") == "success"

                    # 2. Suppression dans le suivi local
                    appts = load_all_appointments()
                    found = None
                    new_appts = []
                    for a in appts:
                        if a.get("date", "") >= "2026-11-24":
                            desc = a.get("description", "").replace(" ", "").replace(".", "").lower()
                            title = a.get("title", "").lower()
                            if (tel and len(tel) >= 8 and tel in desc) or (nom_a and nom_a in title):
                                found = a
                                continue
                        new_appts.append(a)

                    if gcal_deleted or found:
                        save_all_appointments(new_appts)
                        raw_title = (r_del_json.get("title") or (found.get("title") if found else "") or "").strip()
                        titre_affiche = raw_title.replace("\n", " ").strip() or (nom_a_raw if nom_a_raw else "Rendez-vous")

                        date_fr, heure_fr = _formater_date_heure_fr(r_del_json.get("start", ""))
                        if not date_fr and found:
                            date_fr, heure_fr = _formater_date_heure_fr(found.get("date", ""), found.get("time", ""))

                        date_affiche = date_fr or (found.get("date") if found else "date réservée")
                        heure_affiche = heure_fr or (found.get("time", "")[:5].replace(":", "h") if found else "")

                        nom_pour_sms = nom_a_raw
                        if not nom_pour_sms and raw_title:
                            ligne_nom = raw_title.split("\n")[-1].strip()
                            nom_pour_sms = ligne_nom.split("(")[0].strip() or titre_affiche

                        tel_sms = _extraire_mobile_fr(
                            tel,
                            r_del_json.get("description", ""),
                            found.get("description", "") if found else ""
                        )
                        sms_statut = "Non envoyé (pas de mobile 06/07)"
                        if tel_sms:
                            sms_annul = _construire_sms_annulation(nom_pour_sms, date_fr, heure_fr)
                            threading.Thread(target=envoyer_sms_macrodroid, args=(tel_sms, sms_annul), daemon=True).start()
                            sms_statut = f"SMS de confirmation envoyé vers {tel_sms}"

                        horodate = datetime.now(_get_paris_tz()).strftime("%d/%m/%Y à %Hh%M")
                        alert = f"""❌ *RENDEZ-VOUS ANNULÉ PAR LE CLIENT (Par courtoisie)*

🐾 *Rendez-vous libéré* : *{titre_affiche}*
📅 *Date* : *{date_affiche}* {heure_affiche}
🕒 *Horodatage* : {horodate}
🗑️ *Action* : Supprimé automatiquement de Google Agenda CANINS MOBILE !
📲 *Preuve SMS* : {sms_statut}

👉 *Ce créneau est de nouveau libre pour un autre animal sur le site !*"""
                        for cid in ["6022976635", "8771111289"]:
                            send_telegram_message(cid, alert)

                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "success", "message": f"Votre rendez-vous pour {titre_affiche} a bien été annulé dans l'agenda. Merci d'avoir prévenu pour la bonne organisation de la journée de Sandrine."}).encode("utf-8"))
                        return
                    else:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "not_found", "message": "Aucun rendez-vous trouvé avec ce numéro ou ce nom dans l'agenda."}).encode("utf-8"))
                        return
                except Exception as e_annul:
                    log(f"[API ANNULER ERROR] : {e_annul}")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": str(e_annul)}).encode("utf-8"))
                    return

            # 2. Route Webhook Telegram habituelle
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

            update = json.loads(post_data.decode("utf-8"))
            if "callback_query" in update:
                cq = update["callback_query"]
                threading.Thread(target=process_callback_query, args=(cq,), daemon=True).start()
            elif "message" in update:
                m = update["message"]
                m_type = "voice" if "voice" in m else ("text" if "text" in m else "other")
                log(f"Reçu message Telegram ({m_type}) de {m.get('chat', {}).get('id')}")
                threading.Thread(target=process_message, args=(m,), daemon=True).start()
        except Exception as e:
            log(f"Webhook POST Error: {e}")

def process_callback_query(cq: dict):
    """Exécute réellement l'action confirmée par Sandrine via le bouton inline Telegram."""
    cq_id = cq.get("id")
    data_str = cq.get("data", "")
    msg_obj = cq.get("message", {})
    chat_id = str(msg_obj.get("chat", {}).get("id", ""))
    message_id = msg_obj.get("message_id")

    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cq_id}, timeout=8)
    except Exception:
        pass

    if ":" not in data_str:
        return
    decision, act_id = data_str.split(":", 1)
    pending = PENDING_ACTIONS.pop(act_id, None)

    def _edit_or_send(txt):
        if chat_id and message_id:
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                    json={"chat_id": chat_id, "message_id": message_id, "text": txt, "parse_mode": "Markdown"},
                    timeout=10
                )
                if r.status_code == 200:
                    return
            except Exception:
                pass
        if chat_id:
            send_telegram_message(chat_id, txt)

    if not pending:
        _edit_or_send("⏳ Cette demande a expiré (ou le serveur a redémarré). Redemande-moi simplement si besoin.")
        return

    if decision == "cancel":
        if chat_id:
            if chat_id not in USER_STATES:
                USER_STATES[chat_id] = {"historique": [], "last_greeted_date": ""}
            USER_STATES[chat_id]["historique"] = [{
                "role": "assistant",
                "text": "[Action précédente annulée par Sandrine — traiter le prochain message comme une nouvelle demande indépendante]"
            }]
            save_states()
        _edit_or_send("🚫 Action annulée, rien n'a été modifié.")
        return

    analyse = pending.get("analyse", {})
    act = analyse.get("action")

    try:
        if act == "ANNULER":
            nom_a = (analyse.get("nom_animal") or "").strip()
            nom_m_ana = (analyse.get("nom_maitre") or "").strip()
            client_rec = _trouver_client_par_champs(nom_a, nom_m_ana)
            nom_m = nom_m_ana or ((client_rec.get("nom_maitre") if client_rec else "") or "").strip()
            tel_db = ""
            if client_rec:
                tel_db = client_rec.get("tel") or (client_rec.get("tels_clean") or [""])[0]

            payload = {
                "action": "delete",
                "nom_animal": nom_a,
                "nom_maitre": nom_m,
                "telephone": tel_db,
                "date": analyse.get("date", "")
            }
            resp = requests.post(APPS_SCRIPT_URL, json=payload, timeout=20).json()
            if resp.get("status") == "success":
                tel_sms = _extraire_mobile_fr(resp.get("description", ""), tel_db)
                date_fr, heure_fr = _formater_date_heure_fr(resp.get("start") or analyse.get("date", ""))
                nom_a_sms = nom_a or ((client_rec.get("nom_animal") if client_rec else "") or "votre animal").strip()
                if tel_sms:
                    sms_txt = _construire_sms_annulation(nom_a_sms, date_fr, heure_fr)
                    threading.Thread(target=envoyer_sms_macrodroid, args=(tel_sms, sms_txt), daemon=True).start()
                    sms_info = f"\n📲 SMS de confirmation envoyé au client ({tel_sms})."
                else:
                    sms_info = "\nℹ️ Aucun mobile 06/07 trouvé pour envoyer le SMS client."
                if chat_id:
                    _append_history(chat_id, "assistant", f"Annulation effectuée pour {nom_a_sms}.")
                _edit_or_send(f"✅ *Annulation effectuée dans Google Agenda !*\n\n🗑️ {resp.get('message', '')}{sms_info}")
            else:
                _edit_or_send(f"⚠️ *Annulation non effectuée :* {resp.get('message', 'Rendez-vous introuvable')}")

        elif act == "DEPLACER":
            nom_a = (analyse.get("nom_animal") or "").strip()
            nom_m_ana = (analyse.get("nom_maitre") or "").strip()
            client_rec = _trouver_client_par_champs(nom_a, nom_m_ana)
            nom_m = nom_m_ana or ((client_rec.get("nom_maitre") if client_rec else "") or "").strip()
            tel_db = ""
            if client_rec:
                tel_db = client_rec.get("tel") or (client_rec.get("tels_clean") or [""])[0]

            new_date = analyse.get("new_date", "")
            new_heure = analyse.get("new_heure", "09:00")
            dur = int(analyse.get("duree_minutes") or 60)
            start_dt = datetime.strptime(f"{new_date} {new_heure}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=dur)
            payload = {
                "action": "move",
                "nom_animal": nom_a,
                "nom_maitre": nom_m,
                "telephone": tel_db,
                "old_date": analyse.get("old_date", ""),
                "new_start": start_dt.strftime("%Y-%m-%dT%H:%M:00+01:00"),
                "new_end": end_dt.strftime("%Y-%m-%dT%H:%M:00+01:00")
            }
            nouveau_nom = (analyse.get("nouveau_nom_animal") or "").strip()
            if nouveau_nom or client_rec:
                titre_nom = nouveau_nom or ((client_rec.get("nom_animal") if client_rec else "") or nom_a)
                race_c = (client_rec.get("race") if client_rec else "") or ""
                details = [x for x in [nom_m, race_c] if x and x not in ["Inconnu", "Non précisé", "Non précisée", "Client"]]
                details_str = f" ({' - '.join(details)})" if details else ""
                if titre_nom:
                    payload["new_title"] = f"{titre_nom}{details_str}"

            resp = requests.post(APPS_SCRIPT_URL, json=payload, timeout=20).json()
            if resp.get("status") == "success":
                nom_affiche = nouveau_nom or nom_a
                if chat_id:
                    _append_history(chat_id, "assistant", f"Rendez-vous déplacé pour {nom_affiche} au {new_date} à {new_heure}.")
                _edit_or_send(f"✅ *Rendez-vous déplacé dans Google Agenda !*\n\n🐾 *{nom_affiche}* ➔ *{new_date}* à *{new_heure}*")
            else:
                if chat_id:
                    if chat_id not in USER_STATES:
                        USER_STATES[chat_id] = {"historique": [], "last_greeted_date": ""}
                    USER_STATES[chat_id]["historique"] = [{
                        "role": "assistant",
                        "text": f"[Le déplacement précédent pour {nom_a or '?'} a échoué ({resp.get('message', 'introuvable')}). Ne plus relancer de déplacement sauf nouvelle demande explicite.]"
                    }]
                    save_states()
                _edit_or_send(f"❌ *Déplacement impossible :* {resp.get('message', 'Erreur inconnue')}")

        elif act == "CORRIGER_CATEGORIE":
            payload = {
                "action": "update_category",
                "nom_animal": analyse.get("nom_animal", ""),
                "categorie": (analyse.get("categorie") or "B").upper(),
                "race": analyse.get("race", ""),
                "nom_maitre": analyse.get("nom_maitre", "")
            }
            resp = requests.post(APPS_SCRIPT_URL, json=payload, timeout=20).json()
            if resp.get("status") == "success":
                charger_overrides_categories(force=True)
                sheet_info = f"\n📊 [Ouvrir le tableau Google Sheet]({resp['sheet_url']})" if resp.get("sheet_url") else ""
                _edit_or_send(f"✅ *Catégorie enregistrée définitivement !*\n\n🏷️ {resp.get('message', '')}{sheet_info}")
            else:
                _edit_or_send(f"❌ *Erreur lors de la sauvegarde :* {resp.get('message', 'Erreur inconnue')}")

        elif act == "CREER_CLIENT":
            cat_c = (analyse.get("categorie") or "B").upper()
            payload = {
                "action": "save_client",
                "nom_maitre": analyse.get("nom_maitre", "Non précisé"),
                "nom_animal": analyse.get("nom_animal", ""),
                "espece": analyse.get("espece", "Chien"),
                "race": analyse.get("race", "Non précisée"),
                "cat": cat_c,
                "telephone": analyse.get("telephone", ""),
                "secteur": analyse.get("secteur", "Urrugne")
            }
            resp = requests.post(APPS_SCRIPT_URL, json=payload, timeout=20).json()
            if resp.get("status") == "success":
                if isinstance(resp.get("client"), dict):
                    CLIENTS_DB.append(resp["client"])
                sheet_info = f"\n📊 [Ouvrir le tableau Google Sheet]({resp['sheet_url']})" if resp.get("sheet_url") else ""
                _edit_or_send(f"✅ *Nouveau client enregistré dans la base !*\n\n👤 {resp.get('message', '')}{sheet_info}")
                send_telegram_vcf(
                    chat_id,
                    payload["nom_maitre"],
                    payload["nom_animal"],
                    payload["race"],
                    payload["telephone"]
                )
            else:
                _edit_or_send(f"❌ *Erreur lors de la création :* {resp.get('message', 'Erreur inconnue')}")
    except Exception as e_cb:
        log(f"[CALLBACK ERROR] : {e_cb}")
        _edit_or_send(f"❌ *Erreur technique :* {e_cb}")


def watchdog_surveillance_boucle():
    """Robot Sentinelle intégré : surveille l'agenda Google toutes les 5 minutes et alerte si anomalie."""
    time.sleep(30) # Laisse le temps au serveur de démarrer
    while True:
        try:
            live = obtenir_evenements_google_calendar_direct()
            slots = {}
            for ev in live:
                st = ev.get("start", "")
                if st:
                    slots[st] = slots.get(st, []) + [ev.get("title", "Sans titre")]

            # Vérifier les doublons
            doublons = {k: v for k, v in slots.items() if len(v) > 1}
            if doublons:
                msg_d = "🚨 *SENTINELLE LA CANIN MOBILE - ALERTE CONFLIT DÉTECTÉ DANS L'AGENDA !*\n\n"
                for k, v in doublons.items():
                    msg_d += f"📅 Heure : {k}\n🐾 Événements : {', '.join(v)}\n\n"
                msg_d += "👉 Intervention requise."
                for cid in ["6022976635", "8771111289"]:
                    send_telegram_message(cid, msg_d)

            # Envoi automatique des rappels J-1 à 18h00 (uniquement pour les RDV pris >= 2 jours à l'avance)
            paris_tz = timezone(timedelta(hours=1))
            now_paris = datetime.now(paris_tz)
            if now_paris.hour == 18 and now_paris.minute < 15:
                demain = (now_paris + timedelta(days=1)).date()
                for ev in live:
                    ev_id = ev.get("id", "")
                    if not ev_id or ev_id in RAPPELS_ENVOYES:
                        continue
                    st_iso = ev.get("start", "")
                    cr_iso = ev.get("created", "")
                    if not st_iso:
                        continue
                    try:
                        dt_ev = datetime.fromisoformat(st_iso.replace("Z", "+00:00")).astimezone(paris_tz)
                        if dt_ev.date() != demain:
                            continue
                        if cr_iso:
                            dt_cr = datetime.fromisoformat(cr_iso.replace("Z", "+00:00")).astimezone(paris_tz)
                            if (dt_ev.date() - dt_cr.date()).days < 2:
                                continue # Pris la veille pour le lendemain -> pas de rappel inutile
                        desc = ev.get("description", "")
                        m_tel = re.search(r'(0[67](?:[\s\.]?\d{2}){4})', desc)
                        tel_rappel = m_tel.group(1) if m_tel else ""
                        if not tel_rappel:
                            continue
                        titre_clean = ev.get("title", "").replace("\n", " ")
                        h_rap = dt_ev.strftime("%Hh%M")
                        msg_rap = (
                            f"Bonjour ! Petit rappel pour votre RDV demain à {h_rap} ({titre_clean}) avec Sandrine - La Canin Mobile. "
                            f"En cas d'empêchement, vous pouvez annuler ou déplacer ici : https://canin-mobile-bot.onrender.com/reserver#annuler"
                        )
                        if envoyer_sms_macrodroid(tel_rappel, msg_rap):
                            RAPPELS_ENVOYES.add(ev_id)
                    except Exception:
                        pass
        except Exception as e_wd:
            log(f"[WATCHDOG ERROR] : {e_wd}")

        time.sleep(300) # Re-contrôle toutes les 5 minutes

def run_server():
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, WebhookHTTPHandler)
    # Lancement du Robot Sentinelle en tâche de fond permanente
    t_wd = threading.Thread(target=watchdog_surveillance_boucle, daemon=True)
    t_wd.start()
    log("🛡️ Robot Sentinelle de surveillance démarré en tâche de fond (contrôle anti-collision toutes les 5 min).")
    log(f"🚐 SERVEUR WEBHOOK LA CANIN MOBILE (GEMINI 2.5 FLASH DIRECT LOCK) PRÊT SUR LE PORT {PORT}")
    httpd.serve_forever()

if __name__ == "__main__":
    run_server()

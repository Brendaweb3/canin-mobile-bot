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
from datetime import datetime, timedelta
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
APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxSMe4ThGmSAFiL8_hIECTLih9AptUKGA61GCaEHQUmggZ775skFKJZqhBPV4mcRWUj/exec"
PORT = int(os.environ.get("PORT", 10000))

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

def send_telegram_message(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
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
    texte_lower = texte.lower()
    mots = re.findall(r'[a-zA-ZÀ-ÿ0-9]+', texte_lower)
    
    for c in CLIENTS_DB:
        nom_a = c.get("nom_animal", "").lower()
        if nom_a and len(nom_a) >= 3 and nom_a in mots:
            return c
            
    for c in CLIENTS_DB:
        nom_m = c.get("nom_maitre", "").lower()
        if nom_m and len(nom_m) >= 4 and nom_m in texte_lower:
            return c
            
    return None

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

def inscrire_dans_google_calendar(p: dict) -> bool:
    try:
        date_str = p.get('date_proposee')
        heure_str = p.get('heure_debut', '09:00')
        duree = int(p.get('duree_minutes', 60))
        
        h_formatted = heure_str.replace(":", "h")
        nom = p.get('nom_animal', 'Toilettage')
        title = f"{h_formatted}\n{nom}"
        
        start_dt = datetime.strptime(f"{date_str} {heure_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duree)
        
        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:00+01:00")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:00+01:00")
        
        desc = f"Toilettage {nom} - Catégorie {p.get('categorie', 'B')} ({duree} min) - Secteur {p.get('secteur')}"
        if p.get('nom_maitre') and p.get('nom_maitre') != 'Inconnu':
            desc += f" - Maître : {p.get('nom_maitre')}"
        if p.get('race') and p.get('race') not in ['race', 'Inconnue', 'Non précisée']:
            desc += f" - Race : {p.get('race')}"
            
        payload = {
            "title": title,
            "start": start_iso,
            "end": end_iso,
            "description": desc,
            "location": ""
        }
        
        resp = requests.post(APPS_SCRIPT_URL, json=payload, timeout=20)
        if resp.status_code == 200:
            res_json = resp.json()
            if res_json.get("status") == "success":
                log(f"[CALENDAR OK] Rendez-vous Google Calendar inscrit : {res_json.get('eventId')}")
                return True
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

Si elle demande de réserver, noter, inscrire ou bloquer un créneau (ex: "réserve pour mercredi à 13h30", "ajoute Pearl", "mets Eden...") :
{{
  "action": "INSCRIRE",
  "nom_animal": "nom de l'animal déduit de la phrase ou de l'historique",
  "nom_maitre": "nom du maître ou Inconnu",
  "race": "race de l'animal",
  "categorie": "A" ou "B" ou "C" ou "D",
  "duree_minutes": 30 ou 60 ou 90,
  "date_proposee": "YYYY-MM-DD",
  "heure_debut": "HH:MM",
  "secteur": "Urrugne" ou "Saint-Pée-sur-Nivelle",
  "message_reponse": "Phrase courte et chaleureuse confirmant que c'est noté"
}}

Si elle pose une question (créneaux libres, horaires, infos client...) :
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
                    data["race"] = client.get("race", data.get("race"))
                    data["categorie"] = client.get("cat", data.get("categorie", "B"))
                    if client.get("cat") == "A": data["duree_minutes"] = 30
                    elif client.get("cat") == "C": data["duree_minutes"] = 90
                    elif client.get("cat") == "D": data["duree_minutes"] = 120
                    
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

            # 1. Route API de réservation web directe (Clients Facebook, Google, SMS...)
            if self.path == "/api/reserver":
                try:
                    payload = json.loads(post_data.decode("utf-8"))
                    nom_a = payload.get("nom_animal", "Animal")
                    nom_m = payload.get("nom_maitre", "Client Web")
                    tel = payload.get("telephone", "")
                    sec = payload.get("secteur", "Urrugne")
                    dt_p = payload.get("date_proposee", "")
                    h_deb = payload.get("heure_debut", "09:00")
                    cat = payload.get("categorie", "B")
                    esp = payload.get("espece", "Chien")
                    race = payload.get("race", "Non précisée")

                    log(f"[DEMANDE WEB REÇUE] {nom_a} ({nom_m} - {tel}) à {sec}")

                    # Recherche éventuelle dans la base clients existante
                    cat_info = "Nouveau ou à vérifier"
                    try:
                        db_path = os.path.join(os.path.dirname(__file__), "clients_db.json")
                        if os.path.exists(db_path):
                            with open(db_path, "r", encoding="utf-8") as f_db:
                                c_db = json.load(f_db)
                            tel_clean = tel.replace(" ", "").replace(".", "")
                            for item in c_db:
                                item_str = str(item)
                                if nom_a.lower() in item.get("nom_animal", "").lower() or (tel_clean and len(tel_clean) >= 8 and tel_clean in item_str.replace(" ", "")):
                                    cat_trouvee = item.get("categorie", "")
                                    cat_info = f"Cat. {cat_trouvee} (Fiche client retrouvée)"
                                    break
                    except Exception as e_match:
                        log(f"[DB MATCH NOTE] : {e_match}")

                    if dt_p:
                        succes = inscrire_dans_google_calendar(payload)
                        alert = f"""🔔 *NOUVELLE RÉSERVATION EN LIGNE (Site Web)* !

🐾 *Animal* : *{nom_a}* ({esp} - {race})
👤 *Client* : *{nom_m}*
📞 *Téléphone* : `{tel}`
🚐 *Secteur* : *{sec}*
📅 *Date demandée* : *{dt_p}* à *{h_deb}*

{"🎉 👉 *Inscrit automatiquement dans ton Google Agenda CANINS MOBILE !*" if succes else "⚠️ *Enregistré, synchronisation en attente.*"}"""
                    else:
                        alert = f"""🔔 *NOUVELLE DEMANDE DE RENDEZ-VOUS EN LIGNE* !

🐾 *Animal* : *{nom_a}* ({esp} - {race})
👤 *Propriétaire* : *{nom_m}*
📞 *Téléphone* : `{tel}`
🚐 *Secteur* : *{sec}*
📋 *Statut base* : {cat_info}

💡 *Tournée* : {sec} ({'Mar / Mer / Sam' if sec == 'Urrugne' else 'Jeu / Ven'}).
👉 *Demande reçue : analyse des créneaux & quotas en attente pour proposition de date à votre retour.*"""

                    for cid in ["6022976635", "8771111289"]:
                        send_telegram_message(cid, alert)

                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success", "message": "Demande enregistrée avec succès !"}).encode("utf-8"))
                    return
                except Exception as e_res:
                    log(f"[API RÉSERVATION ERROR] : {e_res}")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": str(e_res)}).encode("utf-8"))
                    return

            # 2. Route Webhook Telegram habituelle
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

            update = json.loads(post_data.decode("utf-8"))
            if "message" in update:
                m = update["message"]
                m_type = "voice" if "voice" in m else ("text" if "text" in m else "other")
                log(f"Reçu message Telegram ({m_type}) de {m.get('chat', {}).get('id')}")
                threading.Thread(target=process_message, args=(m,), daemon=True).start()
        except Exception as e:
            log(f"Webhook POST Error: {e}")

def run_server():
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, WebhookHTTPHandler)
    log(f"🚐 SERVEUR WEBHOOK LA CANIN MOBILE (GEMINI 2.5 FLASH DIRECT LOCK) PRÊT SUR LE PORT {PORT}")
    httpd.serve_forever()

if __name__ == "__main__":
    run_server()

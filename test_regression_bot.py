import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import webhook_server as ws


class TestCaninBotRegression(unittest.TestCase):
    def setUp(self):
        self._orig_overrides = dict(ws.CATEGORY_OVERRIDES)

    def tearDown(self):
        ws.CATEGORY_OVERRIDES.clear()
        ws.CATEGORY_OVERRIDES.update(self._orig_overrides)

    def test_creneaux_ignores_stale_json_when_live_ok(self):
        tz = timezone(timedelta(hours=1))
        # Trouver le prochain vendredi à partir d'aujourd'hui (ou aujourd'hui si vendredi matin)
        now_p = datetime.now(tz)
        days_ahead = (4 - now_p.weekday()) % 7
        target_day = (now_p + timedelta(days=days_ahead)).date()
        # Si aujourd'hui est vendredi après 17h, tester le vendredi suivant
        if days_ahead == 0 and now_p.hour >= 17:
            target_day = target_day + timedelta(days=7)

        # Remplir toute la journée de 09h00 à 18h00 en Google Agenda réel
        full_day_events = [
            {
                "start": datetime(target_day.year, target_day.month, target_day.day, h, 0, tzinfo=tz).isoformat(),
                "end": datetime(target_day.year, target_day.month, target_day.day, h + 1, 0, tzinfo=tz).isoformat(),
                "title": f"Client {h}h",
                "description": ""
            }
            for h in range(9, 18)
        ]
        stale_json = [{"date": target_day.strftime("%Y-%m-%d"), "time": "09:00:00", "duration": 60, "description": "Fantôme"}]

        with patch.object(ws, "obtenir_evenements_google_calendar_direct", return_value=full_day_events), \
             patch.object(ws, "load_all_appointments", return_value=stale_json) as mock_load:
            jours = ws.obtenir_tous_les_creneaux_disponibles("Urrugne", 60, "B", max_jours=5)
            mock_load.assert_not_called()
            dates_dispos = [j["date"] for j in jours]
            self.assertNotIn(target_day.strftime("%Y-%m-%d"), dates_dispos)

        # Même si live_events == [], ne pas charger appointments_to_sync.json
        with patch.object(ws, "obtenir_evenements_google_calendar_direct", return_value=[]), \
             patch.object(ws, "load_all_appointments", return_value=stale_json) as mock_load_empty:
            ws.obtenir_tous_les_creneaux_disponibles("Urrugne", 60, "B", max_jours=2)
            mock_load_empty.assert_not_called()

    def test_inscrire_conflict_returns_error_and_no_false_success_telegram(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "conflict", "message": "Créneau déjà pris par gutxi"}

        payload = {
            "nom_animal": "Taïko",
            "nom_maitre": "Dupont",
            "date_proposee": "2026-11-27",
            "heure_debut": "12:00",
            "duree_minutes": 60,
            "categorie": "B",
            "secteur": "Urrugne",
        }
        with patch.object(ws.requests, "post", return_value=mock_resp):
            res = ws.inscrire_dans_google_calendar(payload)
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "conflict")

        # Vérifier que process_message n'envoie jamais ✅ en cas de conflit
        fake_msg = {"chat": {"id": 999999}, "text": "Inscris Taïko aujourd'hui à 12h"}
        fake_analyse = {
            "action": "INSCRIRE",
            "nom_animal": "Taïko",
            "nom_maitre": "Dupont",
            "race": "Bichon",
            "categorie": "B",
            "duree_minutes": 60,
            "date_proposee": "2026-11-27",
            "heure_debut": "12:00",
            "secteur": "Urrugne",
            "message_reponse": "C'est noté.",
        }
        sent_messages = []

        def capture_tg(cid, text, reply_markup=None):
            sent_messages.append(text)

        ws.USER_STATES["999999"] = {"historique": [], "last_greeted_date": ws.get_date_du_jour_fr()}
        with patch.object(ws, "traiter_instruction_avec_memoire", return_value=fake_analyse), \
             patch.object(ws, "trouver_clients_candidats", return_value=[]), \
             patch.object(ws, "inscrire_dans_google_calendar", return_value={"ok": False, "status": "conflict", "message": "Créneau déjà pris par gutxi"}), \
             patch.object(ws, "trouver_premier_creneau_disponible", return_value=("2026-12-01", "09:00", "Mardi 1 Décembre")), \
             patch.object(ws, "send_telegram_message", side_effect=capture_tg), \
             patch.object(ws, "save_states"):
            ws.process_message(fake_msg)

        joined = "\n".join(sent_messages)
        self.assertIn("IMPOSSIBLE D'INSCRIRE CE RENDEZ-VOUS", joined)
        self.assertIn("2026-12-01", joined)
        self.assertNotIn("RENDEZ-VOUS ENREGISTRÉ", joined)

    def test_category_override_applied_on_telegram_inscrire(self):
        ws.CATEGORY_OVERRIDES["falco"] = {
            "nom_animal": "Falco",
            "cat": "B",
            "race": "Berger Australien",
            "nom_maitre": "Martine",
        }
        gemini_json = json.dumps({
            "action": "INSCRIRE",
            "nom_animal": "Falco",
            "nom_maitre": "Martine",
            "race": "Berger Australien",
            "categorie": "D",
            "duree_minutes": 120,
            "date_proposee": "2026-12-05",
            "heure_debut": "16:00",
            "secteur": "Urrugne",
            "message_reponse": "C'est noté pour Falco.",
        })
        mock_gemini = MagicMock()
        mock_gemini.status_code = 200
        mock_gemini.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": gemini_json}]}}]
        }

        with patch.object(ws.requests, "post", return_value=mock_gemini):
            res = ws.traiter_instruction_avec_memoire(
                "Inscris Falco de Martine samedi 5 décembre à 16h",
                [],
                client={"nom_animal": "Falco", "nom_maitre": "Martine", "race": "Berger Australien", "cat": "D", "duree": "120 min", "secteur": "Urrugne"},
                live_events=[]
            )

        self.assertIsNotNone(res)
        self.assertEqual(res["categorie"], "B")
        self.assertEqual(res["duree_minutes"], 60)

    def test_live_prompt_uses_real_google_events_never_static_agenda(self):
        tz = timezone(timedelta(hours=1))
        now_p = datetime.now(tz)
        ev = [{
            "start": now_p.replace(hour=9, minute=0, second=0, microsecond=0).isoformat(),
            "end": now_p.replace(hour=10, minute=0, second=0, microsecond=0).isoformat(),
            "title": "Martine (Falco)",
        }]
        txt = ws.construire_planning_live_pour_ia(ev, nb_jours=3)
        self.assertIn("09h00-10h00 Martine (Falco)", txt)
        self.assertNotIn("Créneau Libre & Pause Déjeuner", txt)

        txt_err = ws.construire_planning_live_pour_ia(None, nb_jours=3)
        self.assertIn("TEMPORAIREMENT INACCESSIBLE", txt_err)

    def test_disambiguation_skipped_when_analyse_already_identifies_client(self):
        fake_msg = {"chat": {"id": 888888}, "text": "Inscris Falco de Martine demain à 10h"}
        candidats = [
            {"nom_animal": "Falco", "nom_maitre": "Martine", "race": "Berger Australien", "cat": "B", "tel": "0600000001"},
            {"nom_animal": "Falco", "nom_maitre": "Dupont", "race": "Labrador", "cat": "C", "tel": "0600000002"},
        ]
        fake_analyse = {
            "action": "INSCRIRE",
            "nom_animal": "Falco",
            "nom_maitre": "Martine",
            "race": "Berger Australien",
            "categorie": "B",
            "duree_minutes": 60,
            "date_proposee": "2026-12-05",
            "heure_debut": "10:00",
            "secteur": "Urrugne",
            "message_reponse": "C'est noté.",
        }
        sent_messages = []
        ws.USER_STATES["888888"] = {"historique": [], "last_greeted_date": ws.get_date_du_jour_fr()}

        with patch.object(ws, "traiter_instruction_avec_memoire", return_value=fake_analyse), \
             patch.object(ws, "trouver_clients_candidats", return_value=candidats), \
             patch.object(ws, "inscrire_dans_google_calendar", return_value={"ok": True, "status": "success", "eventId": "ev123"}), \
             patch.object(ws, "send_telegram_message", side_effect=lambda cid, t, reply_markup=None: sent_messages.append(t)), \
             patch.object(ws, "save_states"):
            ws.process_message(fake_msg)

        joined = "\n".join(sent_messages)
        self.assertNotIn("Plusieurs fiches correspondent", joined)
        self.assertIn("RENDEZ-VOUS ENREGISTRÉ", joined)

    def test_qualifier_categorie_animal_breed_rules(self):
        ws.CATEGORY_OVERRIDES.clear()
        with patch.object(ws, "charger_overrides_categories", return_value=ws.CATEGORY_OVERRIDES):
            cat_a, dur_a, _ = ws.qualifier_categorie_animal(nom_animal="TestMimi99", espece="Chien", race="Chihuahua")
            self.assertEqual((cat_a, dur_a), ("A", 30))

            cat_b, dur_b, _ = ws.qualifier_categorie_animal(nom_animal="TestRex99", espece="Chien", race="Golden Retriever")
            self.assertEqual((cat_b, dur_b), ("B", 60))

            cat_c, dur_c, _ = ws.qualifier_categorie_animal(nom_animal="TestNaya99", espece="Chien", race="Berger Australien")
            self.assertEqual((cat_c, dur_c), ("C", 90))

            cat_d, dur_d, _ = ws.qualifier_categorie_animal(nom_animal="TestOurs99", espece="Chien", race="Terre-Neuve")
            self.assertEqual((cat_d, dur_d), ("D", 120))

            ws.CATEGORY_OVERRIDES["falco"] = {"nom_animal": "Falco", "cat": "B", "race": "Berger Australien", "nom_maitre": "Martine"}
            cat_ov, dur_ov, src_ov = ws.qualifier_categorie_animal(nom_animal="Falco", espece="Chien", race="Berger Australien")
            self.assertEqual((cat_ov, dur_ov), ("B", 60))
            self.assertIn("Correction manuelle", src_ov)

    def test_creneaux_returns_none_when_calendar_unreachable(self):
        with patch.object(ws, "obtenir_evenements_google_calendar_direct", return_value=None), \
             patch.object(ws, "load_all_appointments") as mock_load:
            self.assertIsNone(ws.obtenir_tous_les_creneaux_disponibles("Urrugne", 60, "B"))
            self.assertEqual(ws.trouver_premier_creneau_disponible("Urrugne", 60, "B"), (None, None, None))
            mock_load.assert_not_called()

    def test_api_reserver_never_confirms_on_calendar_conflict_or_outage(self):
        import io
        body = json.dumps({
            "nom_animal": "Taïko",
            "nom_maitre": "Lorenzo",
            "telephone": "0698799263",
            "secteur": "Urrugne",
            "espece": "Chien",
            "race": "Berger Australien",
            "date_choisie": "2026-11-27",
            "heure_choisie": "12:00",
        }).encode("utf-8")

        def _invoke_reserver():
            handler = object.__new__(ws.WebhookHTTPHandler)
            handler.path = "/api/reserver"
            handler.headers = {"Content-Length": str(len(body))}
            handler.rfile = io.BytesIO(body)
            handler.wfile = io.BytesIO()
            status_codes = []
            handler.send_response = lambda code: status_codes.append(code)
            handler.send_header = lambda k, v: None
            handler.end_headers = lambda: None
            handler.do_POST()
            resp_json = json.loads(handler.wfile.getvalue().decode("utf-8"))
            return status_codes[0], resp_json

        # Cas 1 : Google Agenda inaccessible -> HTTP 503 + status error (pas de crash NoneType)
        with patch.object(ws, "charger_overrides_categories", return_value={}), \
             patch.object(ws, "obtenir_evenements_google_calendar_direct", return_value=None):
            code, resp = _invoke_reserver()
            self.assertEqual(code, 503)
            self.assertEqual(resp["status"], "error")

        # Cas 2 : Conflit Google Calendar -> jamais status success, pas de SMS ni d'écriture locale
        with patch.object(ws, "charger_overrides_categories", return_value={}), \
             patch.object(ws, "obtenir_evenements_google_calendar_direct", return_value=[]), \
             patch.object(ws, "inscrire_dans_google_calendar", return_value={"ok": False, "status": "conflict", "message": "Créneau déjà occupé"}), \
             patch.object(ws, "envoyer_sms_macrodroid") as mock_sms, \
             patch.object(ws, "save_all_appointments") as mock_save:
            code, resp = _invoke_reserver()
            self.assertEqual(code, 200)
            self.assertEqual(resp["status"], "error")
            mock_sms.assert_not_called()
            mock_save.assert_not_called()

    def test_inscrire_writes_phone_and_dst_aware_iso(self):
        captured = []

        def _fake_post(url, json=None, timeout=20):
            captured.append(json)
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"status": "success", "eventId": "ev_dst"}
            return r

        with patch.object(ws.requests, "post", side_effect=_fake_post):
            # Hiver (UTC+1)
            ws.inscrire_dans_google_calendar({
                "nom_animal": "Taïko",
                "nom_maitre": "Lorenzo",
                "telephone": "0698799263",
                "secteur": "Urrugne",
                "date_proposee": "2026-12-04",
                "heure_debut": "10:00",
                "duree_minutes": 60,
                "categorie": "B",
            })
            # Été (UTC+2)
            ws.inscrire_dans_google_calendar({
                "nom_animal": "Taïko",
                "nom_maitre": "Lorenzo",
                "telephone": "0698799263",
                "secteur": "Urrugne",
                "date_proposee": "2027-07-01",
                "heure_debut": "10:00",
                "duree_minutes": 60,
                "categorie": "B",
            })

        self.assertIn("0698799263", captured[0]["description"])
        self.assertTrue(captured[0]["start"].endswith("+01:00"))
        # Sur un OS avec ZoneInfo('Europe/Paris'), l'été doit être en +02:00
        tz_p = ws._get_paris_tz()
        if getattr(tz_p, "key", None) == "Europe/Paris":
            self.assertTrue(captured[1]["start"].endswith("+02:00"))

    def test_live_prompt_uses_saint_pee_never_cambo(self):
        txt = ws.construire_planning_live_pour_ia([], nb_jours=10)
        self.assertIn("Saint-Pée-sur-Nivelle", txt)
        self.assertNotIn("Cambo", txt)

    def test_watchdog_detects_partial_overlap(self):
        tz = timezone(timedelta(hours=1))
        now_p = datetime(2026, 11, 27, 8, 0, tzinfo=tz)
        overlapping_events = [
            {"id": "e1", "start": "2026-11-27T11:00:00+01:00", "end": "2026-11-27T12:30:00+01:00", "title": "Chien A"},
            {"id": "e2", "start": "2026-11-27T12:00:00+01:00", "end": "2026-11-27T13:00:00+01:00", "title": "Chien B"},
        ]
        conflits = ws.detecter_chevauchements_live(overlapping_events, now_p)
        self.assertEqual(len(conflits), 1)

        adjacent_events = [
            {"id": "e1", "start": "2026-11-27T11:00:00+01:00", "end": "2026-11-27T12:00:00+01:00", "title": "Chien A"},
            {"id": "e2", "start": "2026-11-27T12:00:00+01:00", "end": "2026-11-27T13:00:00+01:00", "title": "Chien B"},
        ]
        self.assertEqual(len(ws.detecter_chevauchements_live(adjacent_events, now_p)), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

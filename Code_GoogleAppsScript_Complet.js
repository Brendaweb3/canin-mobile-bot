// =========================================================================
// GOOGLE APPS SCRIPT POUR LA CANIN MOBILE (SANDRINE HIRIART)
// Agenda ID : 7617be8edad0ef0e86a8b6c2f0b50ab7cc9d0c60e3cf40a512f40542ed3d849c@group.calendar.google.com
// Version 4 : Lecture + Insertion + Suppression + Déplacement + Base Catégories (Google Sheet)
// =========================================================================

var CALENDAR_ID = "7617be8edad0ef0e86a8b6c2f0b50ab7cc9d0c60e3cf40a512f40542ed3d849c@group.calendar.google.com";
var SHEET_NAME = "CaninMobile_Categories_Animaux";

function normalizeText(txt) {
  if (!txt) return "";
  return txt.toString().toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .trim();
}

function getOrCreateOverridesSheet() {
  var props = PropertiesService.getScriptProperties();
  var sheetId = props.getProperty("OVERRIDES_SHEET_ID");
  var ss = null;
  if (sheetId) {
    try { ss = SpreadsheetApp.openById(sheetId); } catch (e) { ss = null; }
  }
  if (!ss) {
    ss = SpreadsheetApp.create(SHEET_NAME);
    var sh = ss.getActiveSheet();
    sh.setName("Corrections_Categories");
    sh.appendRow(["Animal", "Categorie", "Race", "Maitre", "Date_MAJ"]);
    props.setProperty("OVERRIDES_SHEET_ID", ss.getId());
  }
  return ss;
}

function loadCategoryOverrides() {
  var props = PropertiesService.getScriptProperties();
  var raw = props.getProperty("CATEGORY_OVERRIDES");
  if (!raw) return {};
  try { return JSON.parse(raw); } catch (e) { return {}; }
}

function saveCategoryOverride(nomAnimal, categorie, race, maitre) {
  var key = normalizeText(nomAnimal);
  if (!key) return { status: "error", message: "Nom d'animal manquant" };

  var props = PropertiesService.getScriptProperties();
  var overrides = loadCategoryOverrides();
  var nowStr = Utilities.formatDate(new Date(), "Europe/Paris", "dd/MM/yyyy HH:mm");

  overrides[key] = {
    nom_animal: nomAnimal,
    cat: (categorie || "B").toUpperCase(),
    race: race || "",
    nom_maitre: maitre || "",
    updated: nowStr
  };
  props.setProperty("CATEGORY_OVERRIDES", JSON.stringify(overrides));

  // Miroir dans Google Sheets (lisible comme un Excel)
  var sheetUrl = "";
  try {
    var ss = getOrCreateOverridesSheet();
    sheetUrl = ss.getUrl();
    var sh = ss.getSheets()[0];
    var rows = sh.getDataRange().getValues();
    var updatedRow = false;
    for (var i = 1; i < rows.length; i++) {
      if (normalizeText(rows[i][0]) === key) {
        sh.getRange(i + 1, 1, 1, 5).setValues([[nomAnimal, overrides[key].cat, race || rows[i][2], maitre || rows[i][3], nowStr]]);
        updatedRow = true;
        break;
      }
    }
    if (!updatedRow) {
      sh.appendRow([nomAnimal, overrides[key].cat, race || "", maitre || "", nowStr]);
    }
  } catch (eSheet) {
    // PropertiesService reste valide même si Sheets n'est pas encore autorisé
  }

  return {
    status: "success",
    message: "Catégorie mise à jour : " + nomAnimal + " -> Cat. " + overrides[key].cat,
    sheet_url: sheetUrl,
    override: overrides[key]
  };
}

function findMatchingEvent(cal, targetDate, targetNom, targetTel, eventId) {
  if (eventId) {
    try {
      var byId = cal.getEventById(eventId);
      if (byId) return byId;
    } catch (e) {}
  }

  var searchStart = targetDate ? new Date(targetDate + "T00:00:00+01:00") : new Date();
  var searchEnd = targetDate ? new Date(targetDate + "T23:59:59+01:00") : new Date(Date.now() + 90 * 86400000);
  var events = cal.getEvents(searchStart, searchEnd);
  var normNom = normalizeText(targetNom);
  var cleanTel = (targetTel || "").replace(/\s+/g, "").replace(/\./g, "").trim();

  for (var i = 0; i < events.length; i++) {
    var ev = events[i];
    var evTitle = normalizeText(ev.getTitle());
    var evDesc = normalizeText(ev.getDescription()).replace(/\s+/g, "").replace(/\./g, "");
    var matchAnimal = normNom && evTitle.indexOf(normNom) !== -1;
    var matchTel = cleanTel && cleanTel.length >= 8 && evDesc.indexOf(cleanTel) !== -1;
    if (matchAnimal || matchTel) {
      return ev;
    }
  }
  return null;
}

function jsonOut(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  try {
    if (e && e.parameter && e.parameter.action === "get_overrides") {
      var props = PropertiesService.getScriptProperties();
      return jsonOut({
        status: "success",
        overrides: loadCategoryOverrides(),
        sheet_id: props.getProperty("OVERRIDES_SHEET_ID") || ""
      });
    }

    var cal = CalendarApp.getCalendarById(CALENDAR_ID);
    if (!cal) return jsonOut({ status: "error", message: "Agenda introuvable" });

    var startParam = e && e.parameter && e.parameter.start ? new Date(e.parameter.start) : new Date(2026, 10, 24);
    var endParam = e && e.parameter && e.parameter.end ? new Date(e.parameter.end) : new Date(2027, 2, 1);

    var events = cal.getEvents(startParam, endParam);
    var list = [];

    for (var i = 0; i < events.length; i++) {
      var ev = events[i];
      list.push({
        id: ev.getId(),
        title: ev.getTitle(),
        start: ev.getStartTime().toISOString(),
        end: ev.getEndTime().toISOString(),
        description: ev.getDescription() || ""
      });
    }

    return jsonOut({ status: "success", count: list.length, events: list });
  } catch (err) {
    return jsonOut({ status: "error", message: err.toString() });
  }
}

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);

    // 0. ACTION MISE À JOUR CATÉGORIE ANIMAL (PERSISTANCE GOOGLE)
    if (data.action === "update_category") {
      return jsonOut(saveCategoryOverride(
        data.nom_animal || "",
        data.categorie || "B",
        data.race || "",
        data.nom_maitre || ""
      ));
    }

    var cal = CalendarApp.getCalendarById(CALENDAR_ID);
    if (!cal) return jsonOut({ status: "error", message: "Agenda introuvable" });

    // 1. ACTION DE SUPPRESSION RÉELLE D'UN RENDEZ-VOUS ANNULÉ
    if (data.action === "delete") {
      var evDel = findMatchingEvent(cal, data.date || "", data.nom_animal || "", data.telephone || "", data.eventId || "");
      if (!evDel) {
        return jsonOut({ status: "not_found", deleted_count: 0, message: "Rendez-vous introuvable dans Google Agenda" });
      }
      var deletedTitle = evDel.getTitle();
      evDel.deleteEvent();
      return jsonOut({ status: "success", deleted_count: 1, message: "Supprimé : " + deletedTitle });
    }

    // 2. ACTION DE DÉPLACEMENT SÉCURISÉ D'UN RENDEZ-VOUS EXISTANT
    if (data.action === "move") {
      var evMove = findMatchingEvent(cal, data.old_date || "", data.nom_animal || "", data.telephone || "", data.eventId || "");
      if (!evMove) {
        return jsonOut({ status: "not_found", message: "Rendez-vous d'origine introuvable pour " + (data.nom_animal || "") });
      }
      var newStart = new Date(data.new_start);
      var newEnd = new Date(data.new_end);
      var moveConflicts = cal.getEvents(newStart, newEnd);
      for (var c = 0; c < moveConflicts.length; c++) {
        if (moveConflicts[c].getId() !== evMove.getId()) {
          return jsonOut({ status: "conflict", message: "Créneau déjà occupé par : " + moveConflicts[c].getTitle() });
        }
      }
      evMove.setTime(newStart, newEnd);
      if (data.new_title) {
        evMove.setTitle(data.new_title);
      } else {
        // Met à jour le préfixe horaire du titre existant
        var lines = evMove.getTitle().split("\n");
        var labelPart = lines.length > 1 ? lines.slice(1).join("\n") : evMove.getTitle();
        var shStr = Utilities.formatDate(newStart, "Europe/Paris", "HH'h'mm");
        var ehStr = Utilities.formatDate(newEnd, "Europe/Paris", "HH'h'mm");
        evMove.setTitle(shStr + " à " + ehStr + "\n" + labelPart);
      }
      return jsonOut({ status: "success", eventId: evMove.getId(), message: "Rendez-vous déplacé avec succès" });
    }

    // 3. ACTION D'INSERTION D'UN RENDEZ-VOUS
    var start = new Date(data.start);
    var end = new Date(data.end);

    var conflicts = cal.getEvents(start, end);
    if (conflicts.length > 0) {
      return jsonOut({ status: "conflict", message: "Créneau déjà occupé dans Google Calendar" });
    }

    var event = cal.createEvent(data.title, start, end, {
      description: data.description || "",
      location: data.location || ""
    });

    var text = normalizeText(data.title + " " + (data.description || ""));
    if (text.indexOf("saint-p") !== -1 || text.indexOf("saint pee") !== -1) {
      event.setColor("10"); // Vert
    } else {
      event.setColor("9");  // Bleu
    }

    return jsonOut({ status: "success", eventId: event.getId() });

  } catch (err) {
    return jsonOut({ status: "error", message: err.toString() });
  }
}

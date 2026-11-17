// =========================================================================
// GOOGLE APPS SCRIPT POUR LA CANIN MOBILE (SANDRINE HIRIART)
// Agenda ID : 7617be8edad0ef0e86a8b6c2f0b50ab7cc9d0c60e3cf40a512f40542ed3d849c@group.calendar.google.com
// Version 3 : Lecture temps réel (doGet) + Insertion avec anti-conflit + Suppression réelle (doPost)
// =========================================================================

var CALENDAR_ID = "7617be8edad0ef0e86a8b6c2f0b50ab7cc9d0c60e3cf40a512f40542ed3d849c@group.calendar.google.com";

function doGet(e) {
  try {
    var cal = CalendarApp.getCalendarById(CALENDAR_ID);
    if (!cal) {
      return ContentService.createTextOutput(JSON.stringify({ status: "error", message: "Agenda introuvable" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

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

    return ContentService.createTextOutput(JSON.stringify({
      status: "success",
      count: list.length,
      events: list
    })).setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ status: "error", message: err.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var cal = CalendarApp.getCalendarById(CALENDAR_ID);
    if (!cal) {
      return ContentService.createTextOutput(JSON.stringify({ status: "error", message: "Agenda introuvable" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    // 1. ACTION DE SUPPRESSION RÉELLE D'UN RENDEZ-VOUS ANNULÉ
    if (data.action === "delete") {
      var targetNom = (data.nom_animal || "").toLowerCase().trim();
      var targetTel = (data.telephone || "").replace(/\s+/g, "").replace(/\./g, "").trim();
      var targetDate = data.date || ""; // Format YYYY-MM-DD
      var eventId = data.eventId || "";

      // Si l'ID direct est fourni
      if (eventId) {
        var evById = cal.getEventById(eventId);
        if (evById) {
          evById.deleteEvent();
          return ContentService.createTextOutput(JSON.stringify({ status: "success", message: "Événement supprimé par ID" }))
            .setMimeType(ContentService.MimeType.JSON);
        }
      }

      // Recherche par critères (nom animal / téléphone / date)
      var searchStart = targetDate ? new Date(targetDate + "T00:00:00Z") : new Date(2026, 10, 24);
      var searchEnd = targetDate ? new Date(targetDate + "T23:59:59Z") : new Date(2027, 2, 1);
      var searchEvents = cal.getEvents(searchStart, searchEnd);
      var deletedCount = 0;

      for (var k = 0; k < searchEvents.length; k++) {
        var evToCheck = searchEvents[k];
        var evTitle = evToCheck.getTitle().toLowerCase();
        var evDesc = (evToCheck.getDescription() || "").toLowerCase().replace(/\s+/g, "").replace(/\./g, "");

        var matchAnimal = targetNom && evTitle.indexOf(targetNom) !== -1;
        var matchTel = targetTel && targetTel.length >= 8 && evDesc.indexOf(targetTel) !== -1;

        if (matchAnimal || matchTel) {
          evToCheck.deleteEvent();
          deletedCount++;
        }
      }

      return ContentService.createTextOutput(JSON.stringify({
        status: deletedCount > 0 ? "success" : "not_found",
        deleted_count: deletedCount,
        message: deletedCount + " événement(s) supprimé(s) dans Google Calendar"
      })).setMimeType(ContentService.MimeType.JSON);
    }

    // 2. ACTION D'INSERTION D'UN RENDEZ-VOUS
    var start = new Date(data.start);
    var end = new Date(data.end);

    // ANTI-COLLISION : Vérifier si le créneau est déjà pris physiquement
    var conflicts = cal.getEvents(start, end);
    if (conflicts.length > 0) {
      return ContentService.createTextOutput(JSON.stringify({
        status: "conflict",
        message: "Créneau déjà occupé dans Google Calendar"
      })).setMimeType(ContentService.MimeType.JSON);
    }

    var event = cal.createEvent(data.title, start, end, {
      description: data.description || "",
      location: data.location || ""
    });

    // Code couleur secteur : Urrugne = Myrtille (9), Saint-Pée = Basilic (10)
    var text = (data.title + " " + (data.description || "")).toLowerCase();
    if (text.indexOf("saint-p") !== -1 || text.indexOf("saint pee") !== -1) {
      event.setColor("10"); // Vert
    } else {
      event.setColor("9");  // Bleu
    }

    return ContentService.createTextOutput(JSON.stringify({
      status: "success",
      eventId: event.getId()
    })).setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ status: "error", message: err.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

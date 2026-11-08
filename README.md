# TELENOT GMS für Home Assistant

Custom-Integration für den **Home Assistant Marketplace** (HACS-Katalog) für **TELENOT complex 400 / 400h**, verbunden über ein transparentes **Serial-to-TCP-Gateway**. Zielplattform: **Home Assistant 2026.11 oder neuer** (getestete API-Version: 2026.11.0).

![TELENOT GMS integration icon](custom_components/telenot/brand/icon.png)

[![Im Home Assistant Marketplace öffnen](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=michelde&repository=telenot-hacs&category=integration)
[![Integration in Home Assistant einrichten](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=telenot)

## Funktionsumfang V1

- Lokaler TCP-Zugang, ein zentraler Empfänger, Protokollquittierungen und automatischer Reconnect.
- Auto-Discovery belegter Adressen; Einzelabfragen von Namen, Sicherungsbereichszuordnungen und Meldebereichen.
- **Ein Alarm-Control-Panel je verwendetem Sicherungsbereich**, mit unscharf, intern scharf, extern scharf und Alarm.
- Binary-Sensoren für aktuelle Eingänge, Ausgangszustände, MB-Zustände und MB-Sperren; separate Bereichsdiagnosen.
- Speicherung von Konfiguration, letzten Zuständen und den letzten 100 empfangenen Ereignissen in Home Assistants `.storage`.
- Scharf-/Unscharfschalten nur auf Anforderung und **standardmäßig deaktiviert**.
- Manuelle Aktualisierung der Discovery, Verbindungsdiagnose und redigierte Diagnostics.

**Status:** Erste Implementierung für die Abnahme. Der neue Integrationskern hat an einer complex 400h einen vollständigen lesenden Scan mit **125 erfolgreichen Metadatenabfragen** abgeschlossen (08.11.2026; 1 Sicherungsbereich, 21 Meldebereiche). Eine unvollständige Empfangsfolge wurde nach Timeout verworfen, danach lief der Scan weiter. HA-Einrichtung, Speicherung und Serviceaufrufe sind offline gegen einen Gateway-Simulator getestet. Produktive HA-Abnahme und reale Schaltprüfung stehen noch aus; der Firmwarestand ist noch nicht numerisch erfasst.

V2 ergänzt Ausgänge schalten, MB sperren/entsperren und Alarme rücksetzen. Diese Bedienaktionen sind in V1 nicht verfügbar. Uhrstellen ist nicht Teil des bisherigen Umfangs.

## Installation über den Marketplace

1. Den Badge **Im Home Assistant Marketplace öffnen** benutzen. Er öffnet `michelde/telenot-hacs` in deinem HA und bietet bei Bedarf das Hinzufügen als benutzerdefiniertes Repository des Typs **Integration** an.
2. **TELENOT GMS** herunterladen. Bei Erstinstallation kann eine Config-Flow-Integration unmittelbar eingerichtet werden; ein Neustart ist laut Marketplace-Dokumentation nicht grundsätzlich erforderlich.
3. Den Badge **Integration in Home Assistant einrichten** benutzen oder unter **Einstellungen → Geräte & Dienste → Integration hinzufügen → TELENOT GMS** einrichten.
4. Host oder IP-Adresse des Serial-Gateways und TCP-Port eingeben; Default-Port ist `8234`. Die alte aktive Gatewaylösung zuvor pausieren.

Die Marketplace-Links setzen voraus, dass die Dateien im GitHub-Repository veröffentlicht sind. Aufnahme in den allgemeinen Katalog ist für die Installation per Repository-Link nicht nötig und wird nicht als bereits erfolgt behauptet. Alternativ für die lokale Abnahme `custom_components/telenot/` nach `<HA-Konfiguration>/custom_components/telenot/` kopieren und HA neu starten.

**Updates und Entfernen:** Wenn die Integration bereits geladen ist, ist nach Aktualisierung bzw. Entfernung ein Neustart erforderlich; der Marketplace weist darauf hin. Nur eine Installation mit Domain `telenot` gleichzeitig betreiben. Eine ältere gleichnamige Custom-Integration zunächst sichern und geordnet ersetzen; zwei Repositories derselben Domain können nicht parallel installiert werden.

Ein späterer GitHub-Release kann das Archiv `telenot.zip` enthalten. Es enthält den Komponentenordner `telenot/`; entpackt wird nach `custom_components/`.

### Veröffentlichungsanforderungen

- Ein Repository, eine Integration unter `custom_components/telenot/`; alle Laufzeitdateien liegen dort.
- Manifestversion **0.1.0** und zukünftiger Release-Tag **0.1.0** stimmen überein. Dokumentation und Issue-Tracker zeigen auf dieses Repository.
- Fertige Übersetzungen liegen in `translations/en.json` und `de.json`; kein `strings.json` und keine unaufgelösten Lokalise-Referenzen. Parametrierte Anlagentexte werden nicht übersetzt; Diagnose- und Ersatznamen hingegen schon.
- Kategorie: `security_safety.alarm_systems`. Das eigene Symbol liegt in `brand/icon.png`, nicht im HA-Markendesign.
- Nutzerzustände bleiben unter `.storage`, außerhalb des austauschbaren Integrationsordners. `persistent_directory` ist daher nicht nötig.
- `.github/workflows/validate.yml` führt **hassfest** und **hacs/action** mit `category: integration` aus, ohne Prüfungen zu ignorieren. Der vorhandene Testworkflow bleibt zusätzlich bestehen.
- Der GitHub-Repository-Stand benötigt Beschreibung, Topics und aktivierte Issues. Passende Topics sind beispielsweise `home-assistant`, `custom-integration`, `hacs`, `marketplace` und `telenot`. Für eine spätere Aufnahme in den allgemeinen Katalog sind erfolgreiche Validatorläufe und ein vollständiger Release erforderlich; eine Katalogaufnahme wird separat beantragt.

Grundlage: [Offizielle Marketplace-Anleitung für Custom-Integrationen](https://contributors.home-assistant.io/marketplace/guides/integrations/) und [Validierungsreferenz](https://contributors.home-assistant.io/marketplace/reference/validation/).

## Gateway und bestehende Integration

- Gateway im transparenten TCP-Servermodus; serielle Seite **9600 Baud, 8N1**, keine Software-Flusssteuerung.
- **Nur ein aktiver GMS-Client auf der seriellen Schnittstelle.** Ein Gateway mit mehreren TCP-Verbindungen isoliert die GMS-Handshakes nicht automatisch. Bestehende MQTT-/Docker-Lösung vor der Einrichtung pausieren.
- Der Betrieb nutzt rohe TCP-Bytes; keine Telnet-IAC-Aushandlung und kein RFC2217.
- Die Einrichtungsprüfung liest ID und aktuelle Statusblöcke und schließt ihre Verbindung. Danach übernimmt die Integration mit einer eigenen dauerhaften Sitzung.
- Änderungen an Host/Port über **Neu konfigurieren**. Dabei wird die alte Verbindung geschlossen und geprüft, dass am neuen Zugang dieselbe Panel-ID antwortet.

## Discovery und Zustände

Nach den ersten gültigen Statusrahmen wird der Belegtstatus angefordert. Sobald beide Teile angekommen sind, werden Entitäten angelegt. Namen und Zuordnungen folgen im Hintergrund. Ein Text benötigt einen eigenen Protokollzyklus; ein erster Scan mit vielen Punkten kann **mehrere Minuten** dauern.

Die Diagnoseentität **Discovery** zeigt Status, Fortschritt und fehlgeschlagene Textabfragen. Ein fehlender Name blockiert die Anzeige eines sonst erkannten Punkts nicht. **Refresh discovery** aktualisiert auch bereits gecachte Namen. Bei Wiederverbindung wird die Belegung erneut gelesen; Texte werden für bis zu 24 Stunden wiederverwendet.

- Belegt bedeutet **konfiguriert**, nicht ausgelöst. Ein aktiver Zustand wird bei GMS als Nullbit übertragen.
- Ein ruhiger Eingang löscht keinen gespeicherten Alarm. Ereignisse und aktuelle Zustandsbits bleiben getrennt.
- Nicht mehr belegte Entitäten bleiben in der HA-Registry, werden aber nicht verfügbar; sie können nach Prüfung entfernt werden.
- Ausschließlich spontan gemeldete Quellen erhalten keinen erfundenen kontinuierlichen Binary-Sensor. Ihre Ereignisse werden weiterhin erfasst.
- Diagnose- und Ausgangssensoren sind zum Teil standardmäßig deaktiviert. Bei Bedarf in der HA-Entitätsverwaltung aktivieren.
- Sensortypen wie Fenster/Tür/Bewegung werden nicht aus jeder Adresse erraten. Passende Geräteklassen können in HA angepasst werden.
- Nach Neustart sind gespeicherte Zustände **historische Werte**. Live-Entitäten werden erst mit neuer, bestätigter Belegung und frischen Statusdaten verfügbar. Unbekannt wird niemals als unscharf ausgegeben.

Ein Bereich verwendet acht Adressen für acht Eigenschaften. Diese werden zu **einem** Alarm-System zusammengefasst. Gleiche Statusmuster unbenutzter Bereiche erzeugen keine weiteren Alarm-Panels.

## Scharf-/Unscharfschalten

In den Integrationsoptionen **Scharf- und Unscharfschalten erlauben** aktivieren, wenn die Bedienung ausdrücklich gewünscht ist. Das Aktivieren selbst schaltet nichts.

Danach stehen die üblichen HA-Aktionen zur Verfügung:

- `alarm_control_panel.alarm_arm_home`: intern scharf
- `alarm_control_panel.alarm_arm_away`: extern scharf
- `alarm_control_panel.alarm_disarm`: unscharf

Eine ausdrücklich dafür konfigurierte Automation kann diese Aktionen ebenfalls aufrufen. Die GMS-Schnittstelle besitzt keine Benutzer-PIN-Authentifizierung; ein HA-Codefeld würde nicht automatisch eine Prüfung durch die Zentrale bewirken. Zugriff auf HA und Gateway entsprechend beschränken.

Ein Auftrag wird erst im zulässigen Sendefenster übertragen. **ACK mit Fehlersatz ist ein Fehler.** Nach einem normalen ACK wird auf den passenden neuen Bereichsstatus gewartet. Ein Timeout bedeutet ggf. ungewisse Ausführung, nicht sicher „nicht ausgeführt“; die Integration wiederholt Schaltbefehle nicht automatisch. Aktuelle Anlage prüfen, bevor eine Aktion erneut angefordert wird.

## Ereignisse und Speicherung

Spontane Ereignisse werden als `telenot_event` auf dem HA-Eventbus ausgegeben:

```yaml
event_type: telenot_event
data:
  entry_id: "…"
  panel_id: "000815"
  address: 1
  device_area: 1
  extension: 1
  code: 34
  active: true
  received_at: 0.0
  panel_time: "2026-11-07T12:34:56"
  text: "Point text"
```

`received_at` ist ein Unix-Zeitstempel; `panel_time` ist die lokale Anlagenzeit **ohne übermittelten UTC-Offset**. Text und Anlagenzeit können fehlen. Betreibertexte können personenbezogen sein. Eine lückenlose Ereignisnachlieferung nach Verbindungsausfällen oder Exactly-once-Zustellung wird vom Protokoll nicht gewährleistet.

Der Integrationscache liegt unter `.storage/telenot.<entry_id>` und wird gebündelt bzw. beim Herunterfahren gespeichert. Er enthält Namen, Zustände und maximal 100 Ereignisse. HA-Recorder kann unabhängig davon den Zustandverlauf speichern. Beim Entfernen der Integration wird ihr eigener Cache gelöscht. Es werden keine ausstehenden Schaltaufträge gespeichert oder beim Start nachgeholt.

Metadatenberichte enthalten jetzt den lesbaren Namen unter `name` und die unveränderten Rohbytes unter `text` (Hex). Die zusätzliche `inventory`-Übersicht trennt `input_points` (Meldergruppen und weitere Eingänge), `message_areas`, `security_areas` und `outputs`. Meldepunktnamen werden nicht aus dem zugeordneten Meldebereich übernommen: Beide können unterschiedlich parametriert sein. Leere Ausgangstexte werden ausdrücklich als solche erhalten; Ersatznamen sind als `name_source: fallback` gekennzeichnet.

Ein älterer Bericht mit ausschließlich Hextext kann ohne neue Anlagenverbindung in eine separate lesbare Datei überführt werden:

```sh
python3.14 tools/render_discovery.py diagnostics/core-discovery-ZEITSTEMPEL.json
```

Das Original bleibt unverändert; die neue Datei erhält den Suffix `-readable.json`.

## Textcodierung / Firmware

Die PDF beschreibt ASCII. Am getesteten System kommen 16- und 32-Byte-Texte vor; `0xF5` steht für `ü` und `0xEF` für `ö` (z. B. „Tür“ und „Störung“). Diese beobachteten Zuordnungen werden unterstützt. Andere unbekannte Nicht-ASCII-Bytes werden sichtbar als `�` ausgegeben, ihre Rohbytes bleiben im Metadatencache erhalten. Keine unbestätigte vollständige Zeichentabelle wird angenommen.

Neue Firmware kann zusätzliche belegte Adressen verwenden. Die Integration erhält solche Positionen; sie behauptet daraus keine Schaltfähigkeit. Die Firmwareversion lässt sich mit den dokumentierten Abfragen nicht bestimmen.

## Entwicklung und Tests

Python 3.14 und eine virtuelle Umgebung:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check custom_components tests tools
```

Die Tests verbinden sich ausschließlich mit einem simulierten Gateway auf `127.0.0.1`. Sie prüfen unabhängige PDF-Telegramme, Fragmentierung, ID-Codierung, Adressierung, Discovery, Kollisionen, Fehler-ACK, Reconnect, Befehlsbestätigung sowie tatsächliche HA-2026.11-Einrichtung, Registry und Persistenz.

Die offizielle hassfest-Prüfung kann zusätzlich lokal per Docker ausgeführt werden:

```sh
docker run --rm \
  --mount "type=bind,source=$PWD/custom_components,target=/github/workspace/custom_components,readonly" \
  ghcr.io/home-assistant/hassfest
```

Falls das veröffentlichte Validatorimage nur AMD64 unterstützt, auf Apple Silicon zusätzlich `--platform linux/amd64` verwenden. Die HACS-Action prüft auch den GitHub-Repository-Stand; ein lokal bestandener Test oder hassfest-Lauf ersetzt deren Ergebnis nicht.

Der Protokollkern unter `custom_components/telenot/protocol/` verwendet nur die Python-Standardbibliothek und importiert Home Assistant nicht.

### Vollständige Discovery mit dem neuen Kern

```sh
python3.14 -m custom_components.telenot.protocol --host GATEWAY_IP --port 8234
```

Nur bei freier Gatewayverbindung ausführen. Dieser Befehl verwendet denselben Kern wie die HA-Integration, führt ausschließlich lesende Discovery durch und schließt die Verbindung anschließend wieder. Standardlimit: 15 Minuten; anlagenspezifischer Bericht unter `diagnostics/`. Bei einzelnen abgelehnten Metadatenabfragen wird das Ergebnis als `partial` gemeldet und trotzdem gespeichert.

### Manuelle lesende Diagnose

```sh
python3 tools/gms_probe.py --self-test
python3 tools/gms_probe.py --host GATEWAY_IP --port 8234 --seconds 90 --address 0x0000
```

Nur bei freier Gatewayverbindung ausführen. Dieser unabhängige Diagnose-Prototyp sendet ausschließlich ACKs, SEND_NORM und lesende 0x10-Abfragen. Rohmitschnitte in `diagnostics/` enthalten Anlagendaten und werden nicht in Git aufgenommen.

### Release vorbereiten

```sh
python3 tools/build_release.py
```

Erzeugt `dist/telenot.zip`. Bei einem GitHub-Tag-Build wird ein nicht zur Manifestversion passender Tag zurückgewiesen. Push, Tagging, Repository-Topics und Veröffentlichung erfolgen separat. Die vollständigen Erkenntnisse und der Abnahmeplan stehen in [PLAN.md](PLAN.md).

extends RefCounted
## Fehler und Warnungen des Mods an den Server melden, damit sie dort im Protokoll landen
## (server/src/errlog.js, ansehen mit `docker exec voicigame node src/errlog.js`).
##
## Gelesen wird die Protokolldatei des Spiels (user://logs/godot.log): dort steht alles, auch echte
## Skriptfehler, die kein eigener Aufruf abfangen kann. Geschickt wird nur, was von uns kommt:
## Meldungen mit „Voicigame“ oder mit einer unserer Dateien in der at-Zeile.
##
## Was rausgeht: Text der Meldung und Datei mit Zeilennummer, beides gekürzt. Pfade werden abgeschnitten,
## der Benutzername des Rechners steht also nie drin. Höchstens MAX_SEND Meldungen je Spielstart.

const LOG := "user://logs/godot.log"
const POLL_MS := 10000
const SEND_GAP_MS := 4000
const MAX_SEND := 10
const MAX_MSG := 300
const HEADS := ["ERROR:", "SCRIPT ERROR:", "WARNING:", "USER ERROR:", "USER SCRIPT ERROR:", "USER WARNING:"]

var server_url := ""
var version := ""

var _at := 0                     # so weit ist die Datei gelesen
var _rest := ""                  # angefangene Zeile vom letzten Mal
var _queue: Array = []           # [{msg, where}]
var _sent := 0
var _next_poll := 0
var _next_send := 0
var _http: HTTPRequest = null
var _busy := false


func setup(node: Node, url: String, mod_version: String) -> void:
	server_url = url
	version = mod_version
	_http = HTTPRequest.new()
	_http.timeout = 20.0
	node.add_child(_http)
	_http.request_completed.connect(func(_r, _c, _h, _b): _busy = false)
	# Was vor dem Start des Mods passiert ist, gehört nicht uns
	if FileAccess.file_exists(LOG):
		var f := FileAccess.open(LOG, FileAccess.READ)
		if f:
			_at = f.get_length()
			f.close()


## Jeden Frame aufrufen, macht von selbst nur alle paar Sekunden etwas.
func poll() -> void:
	if _http == null or _sent >= MAX_SEND:
		return
	var now := Time.get_ticks_msec()
	if now >= _next_poll:
		_next_poll = now + POLL_MS
		_read()
	if not _queue.is_empty() and not _busy and now >= _next_send:
		_next_send = now + SEND_GAP_MS
		_send(_queue.pop_front())


## Etwas von Hand melden (für Stellen, die keine Godot-Meldung erzeugen).
func report(text: String, where := "") -> void:
	if _sent < MAX_SEND and _queue.size() < 20:
		_queue.append({"msg": text, "where": where})


# ------------------------------------------------------------------

func _read() -> void:
	if not FileAccess.file_exists(LOG):
		return
	var f := FileAccess.open(LOG, FileAccess.READ)
	if f == null:
		return
	var size := f.get_length()
	if size < _at:
		_at = 0      # Datei wurde neu angefangen
	if size == _at:
		f.close()
		return
	f.seek(_at)
	var text := _rest + f.get_buffer(mini(size - _at, 1 << 18)).get_string_from_utf8()
	_at = f.get_position()
	f.close()
	var lines := text.split("\n")
	_rest = lines[lines.size() - 1]
	for i in lines.size() - 1:
		_take(str(lines[i]).strip_edges(true, true), str(lines[i + 1]) if i + 1 < lines.size() else "")


## Eine Zeile prüfen: Kopfzeile einer Meldung, die von uns stammt? Dann in die Schlange.
func _take(line: String, next_line: String) -> void:
	var head := ""
	for h in HEADS:
		if line.begins_with(h):
			head = h
			break
	if head == "":
		return
	var msg := line.substr(head.length()).strip_edges()
	var at := next_line.strip_edges()
	var ours := msg.contains("Voicigame") or at.to_lower().contains("voicigame/") or at.to_lower().contains("voicigame\\")
	if not ours:
		return
	var kind := "Warnung" if head.contains("WARNING") else "Fehler"
	report("%s: %s" % [kind, msg.trim_prefix("Voicigame: ").left(MAX_MSG)], _place(at))


## Aus „at: GDScript::reload (C:/.../mod/voicigame/join_screen.gd:315)“ wird „voicigame/join_screen.gd:315“.
## Alles vor unserem Ordner fällt weg, der Benutzername des Rechners geht so nie raus.
func _place(at: String) -> String:
	var s := at
	var open := s.rfind("(")
	if open >= 0:
		s = s.substr(open + 1).trim_suffix(")")
	s = s.replace("\\", "/")
	var i := s.to_lower().rfind("voicigame/")
	# Steht keine unserer Dateien drin (z. B. push_warning), sagt die Stelle nichts: dann lieber nichts
	return s.substr(i).left(120) if i >= 0 else ""


func _send(item: Dictionary) -> void:
	if server_url == "":
		return
	_busy = true
	_sent += 1
	var body := JSON.stringify({"msg": str(item.get("msg", "")), "where": str(item.get("where", "")),
		"client": "game", "version": version})
	var err := _http.request(server_url + "/api/log", ["Content-Type: application/json"],
		HTTPClient.METHOD_POST, body)
	if err != OK:
		_busy = false

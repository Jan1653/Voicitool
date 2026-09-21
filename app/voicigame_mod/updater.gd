extends Node
## Auto-Update für Mods, die nicht über Voicitool installiert sind (z. B. von GameBanana).
## Beim Spielstart den Server fragen (GET /api/mod). Hat er eine neuere Version, alle geänderten Dateien laden,
## jede Prüfsumme prüfen und erst dann die alten ersetzen. Die neue Version gilt ab dem nächsten Spielstart.
## Nicht bei: Installation über Voicitool (voicitool.cfg liegt daneben, Voicitool aktualisiert selbst),
## Entwicklerkopie im Git-Repo, Testläufen, [update] auto=false in voicigame.cfg. Nie auf eine ältere Version.

signal finished(state: String)

const CONFIG := "user://voicigame.cfg"

var server_url := ""
var mod_dir := ""
var local_version := ""
var state := "none"          # none, installed (gilt ab dem nächsten Start), available (konnte nicht schreiben)
var new_version := ""
var _http: HTTPRequest
var _todo: Array = []
var _got := {}               # Dateiname -> Inhalt


func start(url: String, dir: String, version: String) -> void:
	server_url = url.trim_suffix("/")
	mod_dir = dir
	local_version = version
	if not _allowed():
		return
	_http = HTTPRequest.new()
	_http.timeout = 30.0
	add_child(_http)
	_http.request_completed.connect(_on_manifest, CONNECT_ONE_SHOT)
	_http.request(server_url + "/api/mod")


func _allowed() -> bool:
	if OS.get_environment("VOICIGAME_TEST") != "" and OS.get_environment("VOICIGAME_TEST_UPDATE") == "":
		return false
	if FileAccess.file_exists(mod_dir.path_join("voicitool.cfg")):
		return false
	if DirAccess.dir_exists_absolute(mod_dir.get_base_dir().get_base_dir().path_join(".git")):
		return false
	var cfg := ConfigFile.new()
	if cfg.load(CONFIG) == OK and str(cfg.get_value("update", "auto", "true")).to_lower() in ["false", "0", "no"]:
		return false
	return true


## a neuer als b? Versionen wie 0.3.0
static func newer(a: String, b: String) -> bool:
	var pa := a.split(".")
	var pb := b.split(".")
	for i in maxi(pa.size(), pb.size()):
		var x := int(pa[i]) if i < pa.size() else 0
		var y := int(pb[i]) if i < pb.size() else 0
		if x != y:
			return x > y
	return false


func _on_manifest(_result: int, code: int, _h: PackedStringArray, body: PackedByteArray) -> void:
	var m = JSON.parse_string(body.get_string_from_utf8()) if code == 200 else null
	if not m is Dictionary or not newer(str(m.get("version", "")), local_version):
		_done("none")
		return
	new_version = str(m.version)
	for f in m.get("files", []):
		var file_name := str(f.get("name", ""))
		if not _safe_name(file_name):
			continue
		var path := mod_dir.path_join(file_name)
		if FileAccess.file_exists(path) and FileAccess.get_sha256(path) == str(f.get("sha256", "")):
			continue
		_todo.append(f)
	if _todo.is_empty():
		_done("none")
		return
	print("Voicigame | Update auf %s: %d Dateien werden geladen" % [new_version, _todo.size()])
	_http.request_completed.connect(_on_file)
	_next()


static func _safe_name(n: String) -> bool:
	return n != "" and not "/" in n and not "\\" in n and not ".." in n and (n.ends_with(".gd") or n == "lang.json")


func _next() -> void:
	if _todo.is_empty():
		_apply()
		return
	_http.request(server_url + "/api/mod/" + str(_todo[0].name).uri_encode())


func _on_file(_result: int, code: int, _h: PackedStringArray, body: PackedByteArray) -> void:
	var f = _todo.pop_front()
	if code != 200 or _sha256(body) != str(f.sha256):
		push_warning("Voicigame: Update-Datei %s ungültig (%d), Update abgebrochen" % [f.name, code])
		_done("available")   # lieber gar nicht als nur zum Teil
		return
	_got[str(f.name)] = body
	_next()


static func _sha256(b: PackedByteArray) -> String:
	var h := HashingContext.new()
	h.start(HashingContext.HASH_SHA256)
	h.update(b)
	return h.finish().hex_encode()


## Erst alle neuen Dateien daneben schreiben. Nur wenn das klappt, die alten ersetzen.
func _apply() -> void:
	for file_name in _got:
		var fa := FileAccess.open(mod_dir.path_join(file_name + ".new"), FileAccess.WRITE)
		if fa == null:
			_cleanup()
			_done("available")   # Ordner schreibgeschützt (z. B. Steam unter Programme)
			return
		fa.store_buffer(_got[file_name])
		fa.close()
	for file_name in _got:
		var path := mod_dir.path_join(file_name)
		if FileAccess.file_exists(path):
			DirAccess.remove_absolute(path)
		if DirAccess.rename_absolute(path + ".new", path) != OK:
			push_warning("Voicigame: %s nicht ersetzt" % file_name)
	print("Voicigame | Update auf %s installiert (%d Dateien), gilt ab dem nächsten Start" % [new_version, _got.size()])
	_done("installed")


func _cleanup() -> void:
	for file_name in _got:
		var tmp := mod_dir.path_join(file_name + ".new")
		if FileAccess.file_exists(tmp):
			DirAccess.remove_absolute(tmp)


func _done(s: String) -> void:
	state = s
	if s == "available":
		print("Voicigame | Neue Version %s verfügbar, aber nicht installiert" % new_version)
	finished.emit(s)

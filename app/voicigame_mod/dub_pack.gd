extends Node
## Mitspieler-PC im Dub-Modus: das Pack des Hosts auf diesem PC finden oder einmal herunterladen.
## Erkannt wird es am Fingerabdruck aus Dateinamen und -größen, genau wie am Server (fileInfoOf in server/src/dub.js).
## Heruntergeladene Packs landen im Pack-Ordner des Spiels und sind danach auch sonst spielbar.

signal found(path: String)                  # Pack liegt vollständig in diesem Ordner
signal progress(done_bytes: int, total_bytes: int)
signal failed(message: String)

const PACKS := "user://game/packs_voice/"
const SKIP := ["desktop.ini", "thumbs.db"]
const OWN_SUFFIX := " (Voicigame)"          # gleichnamiger Ordner mit anderem Inhalt: nicht anfassen, eigener Ordner

var client: Node                            # join_client.gd
var info: Dictionary = {}                   # „pack“ aus pack.json: fp, folder, title, files [{name, size, have}]
var _dir := ""
var _http: HTTPRequest
var _poll: HTTPRequest
var _current := {}                          # Datei, die gerade lädt
var _tries := 0
var _stopped := false


## Dateien eines Ordners wie beim Hochladen (dub_hook.gd _start_upload): [{name, size}]
static func files_of(dir: String) -> Array:
	var out := []
	for f in DirAccess.get_files_at(dir):
		if f.begins_with(".") or f.to_lower() in SKIP or f.ends_with(".import") or f.ends_with(".part"):
			continue
		var fa := FileAccess.open(dir + f, FileAccess.READ)
		out.append({"name": f, "size": fa.get_length() if fa else 0})
	return out


## Fingerabdruck wie am Server: nach Namen sortiert, „Name:Größe“ je Zeile, SHA-1, die ersten 16 Zeichen.
static func fingerprint(files: Array) -> String:
	var sorted := files.duplicate()
	sorted.sort_custom(func(a, b): return str(a.name) < str(b.name))
	return "\n".join(sorted.map(func(f): return "%s:%d" % [str(f.name), int(f.size)])).sha1_text().left(16)


## Liegt das Pack schon hier? Erst gleichnamige Ordner, dann alle anderen mit gleich großem Video. -> Pfad oder ""
func find_local() -> String:
	var fp := str(info.get("fp", ""))
	if fp == "" or not DirAccess.dir_exists_absolute(PACKS):
		return ""
	var folder := str(info.get("folder", ""))
	var dirs: Array = []
	if folder != "":
		dirs.append(PACKS + folder + "/")
		dirs.append(PACKS + folder + OWN_SUFFIX + "/")
	for d in DirAccess.get_directories_at(PACKS):
		if not (PACKS + d + "/") in dirs:
			dirs.append(PACKS + d + "/")
	var video := _video_size()
	for p in dirs:
		if not DirAccess.dir_exists_absolute(p):
			continue
		if video >= 0 and not _has_video(p, video):
			continue   # günstiger Vorab-Test, bevor alle Dateien gezählt werden
		if fingerprint(files_of(p)) == fp:
			return p
	return ""


func _video_size() -> int:
	for f in info.get("files", []):
		if str(f.name).get_basename().to_lower() == "dub_video":
			return int(f.size)
	return -1


static func _has_video(dir: String, size: int) -> bool:
	for f in DirAccess.get_files_at(dir):
		if f.get_basename().to_lower() == "dub_video":
			var fa := FileAccess.open(dir + f, FileAccess.READ)
			if fa and fa.get_length() == size:
				return true
	return false


# ------------------------------------------------------------------
# Herunterladen
# ------------------------------------------------------------------

## Pack in den Pack-Ordner des Spiels laden. Dateien, die der Server noch nicht hat, kommen nach (alle 2 s nachsehen).
func download() -> void:
	var folder := str(info.get("folder", "")).validate_filename()
	if folder == "":
		folder = str(info.get("title", "Pack")).validate_filename()
	_dir = PACKS + folder + "/"
	if DirAccess.dir_exists_absolute(_dir) and not _is_partial_of_this(_dir):
		_dir = PACKS + folder + OWN_SUFFIX + "/"
		if DirAccess.dir_exists_absolute(_dir) and not _is_partial_of_this(_dir):
			OS.move_to_trash(ProjectSettings.globalize_path(_dir))   # unser eigener Ordner von einem anderen Pack
	DirAccess.make_dir_recursive_absolute(_dir)
	if _http == null:
		_http = HTTPRequest.new()
		_http.timeout = 600.0
		_http.request_completed.connect(_on_file_done)
		add_child(_http)
		_poll = HTTPRequest.new()
		_poll.timeout = 20.0
		_poll.request_completed.connect(_on_poll_done)
		add_child(_poll)
	_stopped = false
	_next()


func stop() -> void:
	_stopped = true
	if _http:
		_http.cancel_request()
	if not _current.is_empty():
		_trash_part(str(_current.name))
	_current = {}


## Ist der Ordner ein angefangener Download genau dieses Packs? (nur Dateien aus dem Pack, keine mit anderer Größe)
func _is_partial_of_this(dir: String) -> bool:
	var want := {}
	for f in info.get("files", []):
		want[str(f.name)] = int(f.size)
	for f in files_of(dir):
		if not want.has(str(f.name)) or int(want[str(f.name)]) < int(f.size):
			return false
	return true


func _missing() -> Array:
	var have := {}
	for f in files_of(_dir):
		have[str(f.name)] = int(f.size)
	return info.get("files", []).filter(func(f): return int(have.get(str(f.name), -1)) != int(f.size))


func _next() -> void:
	if _stopped or not _current.is_empty():
		return
	var missing := _missing()
	var total := 0
	var left := 0
	for f in info.get("files", []):
		total += int(f.size)
	for f in missing:
		left += int(f.size)
	progress.emit(total - left, total)
	if missing.is_empty():
		if fingerprint(files_of(_dir)) == str(info.get("fp", "")):
			found.emit(_dir)
		else:
			failed.emit("fingerprint")
		return
	var ready := missing.filter(func(f): return f.get("have", true))
	if ready.is_empty():
		get_tree().create_timer(2.0).timeout.connect(_refresh_info)   # der Host lädt es gerade hoch
		return
	_current = ready[0]
	_http.download_file = ProjectSettings.globalize_path(_dir + str(_current.name) + ".part")
	var url := "%s/api/rooms/%s/dub/file/%s?t=%s" % [client.server_url, client.code, str(_current.name).uri_encode(), str(client.token).uri_encode()]
	if _http.request(url) != OK:
		_on_file_done(HTTPRequest.RESULT_CANT_CONNECT, 0, PackedStringArray(), PackedByteArray())


func _on_file_done(result: int, code: int, _h: PackedStringArray, _b: PackedByteArray) -> void:
	if _current.is_empty():
		return
	var name := str(_current.name)
	var part := _dir + name + ".part"
	_current = {}
	if result == HTTPRequest.RESULT_SUCCESS and code == 200 and FileAccess.file_exists(part):
		if FileAccess.file_exists(_dir + name):
			OS.move_to_trash(ProjectSettings.globalize_path(_dir + name))
		DirAccess.rename_absolute(part, _dir + name)
		_tries = 0
	else:
		_trash_part(name)
		_tries += 1
		push_warning("Voicigame: Pack-Datei %s nicht geladen (%d, %d), Versuch %d" % [name, result, code, _tries])
		if _tries > 8:
			failed.emit("download")
			return
		await get_tree().create_timer(minf(20.0, 1.5 * _tries)).timeout
		if code == 404:
			_refresh_info()   # Datei ist doch noch nicht da: Liste neu holen
			return
	_next()


func _trash_part(name: String) -> void:
	var part := _dir + name + ".part"
	if FileAccess.file_exists(part):
		OS.move_to_trash(ProjectSettings.globalize_path(part))


## Welche Dateien hat der Server inzwischen? (pack.json neu holen)
func _refresh_info() -> void:
	if _stopped or _poll.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		return
	_poll.request("%s/api/rooms/%s/dub/pack.json?t=%s" % [client.server_url, client.code, str(client.token).uri_encode()])


func _on_poll_done(_result: int, code: int, _h: PackedStringArray, body: PackedByteArray) -> void:
	var j = JSON.parse_string(body.get_string_from_utf8()) if code == 200 else null
	var pack = j.get("pack") if j is Dictionary else null
	if pack is Dictionary and str(pack.get("fp", "")) == str(info.get("fp", "")):
		info = pack
	_next()

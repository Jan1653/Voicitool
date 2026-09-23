extends RefCounted
## Baut aus einem Pack-Ordner ein Voicigame-Pack (.vgpack) für den Browser: Video als H.264-MP4,
## Ton als AAC, Bilder als WebP. Alles in einer Datei, Aufbau siehe server/src/vgpack.js.
##
## Warum: der Server hat zwei Kerne. Wandelt er das Video selbst um, dauert das lange und bremst
## alles andere aus. Hier läuft es auf dem PC, der das Pack sowieso schon hat, und hochgeladen wird
## danach ein Bruchteil der Daten (Theora und WAV sind ein Vielfaches von H.264 und AAC).
##
## Ohne ffmpeg passiert nichts: dann geht alles wie vorher (Originaldateien hoch, Server wandelt um).
## ffmpeg wird gesucht in: voicitool.cfg ([tools] ffmpeg), PATH, user://voicigame/ffmpeg(.exe).
##
## Benutzung (dub_hook.gd):
##   var b := PackWeb.new()
##   b.start(pack_dir, files, play_order)
##   b.poll()          jeden Frame, bis state in ["done", "error"]
##   b.out_file        fertige Datei, b.progress 0..1

const I18n = preload("i18n.gd")

const AUDIO := ["ogg", "wav", "mp3", "flac", "m4a", "opus", "aac"]
const IMAGE := ["png", "jpg", "jpeg", "webp", "bmp"]
const VIDEO := ["ogv", "mp4", "webm", "mkv", "mov"]
const WORK := "user://voicigame/web"
const BATCH := 16                 # so viele Dateien je ffmpeg-Aufruf
const KEEP := 3                   # so viele fertige Packs bleiben liegen
const MAX_IMAGE := 32 * 1024      # größere Bilder werden zu WebP (aus 200 KB PNG werden ~15 KB)
const IMAGE_W := 960

var state := ""                   # "" | convert | pack | done | error
var progress := 0.0
var error := ""
var out_file := ""
var src_fp := ""                  # Fingerabdruck des Originalpacks (wie auf dem Server)

var _ffmpeg := ""
var _dir := ""
var _work := ""
var _steps: Array = []            # offene ffmpeg-Aufrufe: [{args, out: [{n, file, m, d}]}]
var _step := 0
var _total := 0
var _pid := -1
var _started := 0
var _assets: Array = []           # fertig: [{n, file, m, d}]
var _copy: Array = []             # ohne ffmpeg übernehmen: [{n, file, m, d}]


static var _ff_checked := false
static var _ff_path := ""


## Ist ffmpeg da? Ohne geht der Browser-Weg nicht. Einmal je Sitzung gesucht.
static func ffmpeg_path() -> String:
	if not _ff_checked:
		_ff_checked = true
		_ff_path = _find_ffmpeg()
	return _ff_path


static func _find_ffmpeg() -> String:
	var env := OS.get_environment("VG_FFMPEG")
	if env != "" and FileAccess.file_exists(env):
		return env
	var cfg := ConfigFile.new()
	if cfg.load(I18n.base_dir.path_join("voicitool.cfg")) == OK:
		var p := str(cfg.get_value("tools", "ffmpeg", ""))
		if p != "" and FileAccess.file_exists(p):
			return p
	var own := "user://voicigame/ffmpeg.exe" if OS.get_name() == "Windows" else "user://voicigame/ffmpeg"
	if FileAccess.file_exists(own):
		return ProjectSettings.globalize_path(own)
	# Im PATH: einmal aufrufen und schauen, ob es geht
	var out: Array = []
	if OS.execute("ffmpeg", ["-version"], out, false) == 0:
		return "ffmpeg"
	return ""


## Fingerabdruck einer Dateiliste, genau wie fileInfoOf in server/src/dub.js.
static func fingerprint(files: Array) -> String:
	var lines: Array = []
	for f in files:
		lines.append("%s:%d" % [str(f["name"]), int(f["size"])])
	lines.sort()
	return "\n".join(PackedStringArray(lines)).sha1_text().left(16)


## Ordner für fertige Packs als echter Pfad: ffmpeg kennt user:// nicht.
static func root() -> String:
	DirAccess.make_dir_recursive_absolute(WORK)
	return ProjectSettings.globalize_path(WORK)


## Fertiges Pack von früher, sonst "".
static func cached_file(fp: String) -> String:
	if fp == "":
		return ""
	var p := "%s/%s.vgpack" % [root(), fp]
	return p if FileAccess.file_exists(p) else ""


## Verzeichnis eines fertigen Packs lesen (Aufbau siehe server/src/vgpack.js). {} = geht nicht.
static func manifest_of(file: String) -> Dictionary:
	var f := FileAccess.open(file, FileAccess.READ)
	if f == null:
		return {}
	var head := f.get_buffer(12)
	if head.size() < 12 or head.slice(0, 6).get_string_from_utf8() != "VGPACK" or head[6] != 1:
		f.close()
		return {}
	var len := head.decode_u32(8)
	var text := f.get_buffer(len).get_string_from_utf8()
	f.close()
	var j = JSON.parse_string(text)
	return j if j is Dictionary else {}


## Eine Datei aus dem fertigen Pack herausschneiden. -> hat geklappt?
static func extract(file: String, name: String, out: String) -> bool:
	var m := manifest_of(file)
	if m.is_empty():
		return false
	for a in m.get("assets", []):
		if not a is Dictionary or str(a.get("n", "")) != name:
			continue
		var src := FileAccess.open(file, FileAccess.READ)
		if src == null:
			return false
		DirAccess.make_dir_recursive_absolute(out.get_base_dir())
		var dst := FileAccess.open(out, FileAccess.WRITE)
		if dst == null:
			src.close()
			return false
		src.seek(int(a.get("o", 0)))
		var left := int(a.get("l", 0))
		while left > 0:
			var part := src.get_buffer(mini(left, 1 << 20))
			if part.is_empty():
				break
			dst.store_buffer(part)
			left -= part.size()
		src.close()
		dst.close()
		return left == 0
	return false


## files: [{name, path, size}] aus dem Pack-Ordner, play: Reihenfolge der Zeilen (Dateinamen ohne Endung).
func start(pack_dir: String, files: Array, play: Array) -> void:
	_dir = ProjectSettings.globalize_path(pack_dir)   # ffmpeg kennt user:// nicht
	src_fp = fingerprint(files)
	out_file = "%s/%s.vgpack" % [root(), src_fp]
	if FileAccess.file_exists(out_file):
		_touch(out_file)
		state = "done"
		progress = 1.0
		return
	_ffmpeg = ffmpeg_path()
	if _ffmpeg == "":
		state = "error"
		error = "ffmpeg"
		return
	_work = "%s/build_%s" % [root(), src_fp.left(8)]
	_clear(_work)
	DirAccess.make_dir_recursive_absolute(_work)
	_plan(files, play)
	print("Voicigame | Web-Pack: %d ffmpeg-Aufrufe, %d Dateien direkt (%s)" % [_steps.size(), _copy.size(), _ffmpeg])
	state = "convert" if not _steps.is_empty() else "pack"
	_total = _steps.size()
	progress = 0.0


## Aufräumen, wenn abgebrochen wird.
func cancel() -> void:
	if _pid > 0 and OS.is_process_running(_pid):
		OS.kill(_pid)
	_pid = -1
	if _work != "":
		_clear(_work)
	state = ""


func poll() -> void:
	if state == "convert":
		_poll_convert()
	elif state == "pack":
		_write_pack()


# ------------------------------------------------------------------ Plan

func _plan(files: Array, play: Array) -> void:
	# Reihenfolge im Pack: Video zuerst, dann die Zeilen in Spielreihenfolge, dann der Rest.
	# So kann der Browser schon spielen, während der Rest noch hochlädt.
	var ranked := []
	for f in files:
		var name := str(f["name"])
		var ext := name.get_extension().to_lower()
		var base := name.get_basename()
		var rank := 3 + play.size()
		if base.to_lower() == "dub_video" and ext in VIDEO:
			rank = 0
		elif play.find(base) >= 0:
			rank = 2 + play.find(base)
		elif base.to_lower() == "_backing_track":
			rank = 1
		if ext in AUDIO or ext in IMAGE or ext in VIDEO:
			ranked.append({"name": name, "rank": rank, "ext": ext})
	ranked.sort_custom(func(a, b): return a["rank"] < b["rank"])

	var jobs: Array = []   # [{n, ext, kind}]
	for r in ranked:
		var name: String = r["name"]
		var ext: String = r["ext"]
		var src := _dir.path_join(name)
		var size := 0
		var fa := FileAccess.open(src, FileAccess.READ)
		if fa:
			size = fa.get_length()
			fa.close()
		if ext in VIDEO:
			if ext == "mp4":
				_copy.append({"n": name, "file": src, "m": "video/mp4", "d": 0.0})
			else:
				jobs.append({"n": name, "kind": "video"})
		elif ext in AUDIO:
			if ext in ["m4a", "aac"]:
				_copy.append({"n": name, "file": src, "m": "audio/mp4", "d": 0.0})
			else:
				jobs.append({"n": name, "kind": "audio"})
		elif ext in IMAGE:
			if size <= MAX_IMAGE:
				_copy.append({"n": name, "file": src, "m": _mime(ext), "d": 0.0})
			else:
				jobs.append({"n": name, "kind": "image"})

	# Das Video allein (dauert am längsten), Ton und Bilder in Gruppen
	var group: Array = []
	for j in jobs:
		if j["kind"] == "video":
			_steps.append(_video_step(j["n"]))
			continue
		group.append(j)
		if group.size() >= BATCH:
			_steps.append(_batch_step(group))
			group = []
	if not group.is_empty():
		_steps.append(_batch_step(group))


func _video_step(name: String) -> Dictionary:
	var out := _work.path_join("v.mp4")
	var args := ["-hide_banner", "-nostdin", "-y", "-threads", "2", "-i", _dir.path_join(name),
		"-map", "0:v:0", "-an", "-vf", "scale=-2:'trunc(min(720,ih)/2)*2',format=yuv420p",
		"-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-movflags", "+faststart", out]
	return {"args": args, "out": [{"n": name, "file": out, "m": "video/mp4", "d": 0.0}]}


func _batch_step(group: Array) -> Dictionary:
	var args := ["-hide_banner", "-nostdin", "-y", "-threads", "2"]
	var outs: Array = []
	for j in group:
		args.append("-i")
		args.append(_dir.path_join(str(j["n"])))
	for i in group.size():
		var j: Dictionary = group[i]
		var name := str(j["n"])
		var out := _work.path_join("%d.%s" % [_steps.size() * BATCH + i, "m4a" if j["kind"] == "audio" else "webp"])
		if j["kind"] == "audio":
			args.append_array(["-map", "%d:a:0" % i, "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out])
			outs.append({"n": name, "file": out, "m": "audio/mp4", "d": 0.0})
		else:
			args.append_array(["-map", "%d:v:0" % i, "-vf", "scale='min(%d,iw)':-2" % IMAGE_W,
				"-c:v", "libwebp", "-quality", "80", "-frames:v", "1", out])
			outs.append({"n": name, "file": out, "m": "image/webp", "d": 0.0})
	return {"args": args, "out": outs}


# ------------------------------------------------------------------ Umwandeln

func _poll_convert() -> void:
	if _pid > 0:
		if OS.is_process_running(_pid):
			return
		var code := OS.get_process_exit_code(_pid)
		_pid = -1
		if code != 0:
			push_warning("Voicigame: ffmpeg %d bei Schritt %d: %s" % [code, _step, " ".join(PackedStringArray(_steps[_step]["args"]))])
		# Was da ist, wird übernommen. Eine Datei, die ffmpeg nicht geschafft hat, bleibt im Original.
		for o in _steps[_step]["out"]:
			if FileAccess.file_exists(str(o["file"])):
				_assets.append(o)
			else:
				var src := _dir.path_join(str(o["n"]))
				_copy.append({"n": o["n"], "file": src, "m": _mime(str(o["n"]).get_extension().to_lower()), "d": 0.0})
		_step += 1
		progress = float(_step) / maxf(1.0, float(_total))
		if _step >= _steps.size():
			state = "pack"
			return
	_pid = OS.create_process(_ffmpeg, _steps[_step]["args"])
	if _pid <= 0:
		state = "error"
		error = "start"
		return
	_started = Time.get_ticks_msec()
	_lower_priority(_pid)


## Der Host spielt gleichzeitig: ffmpeg läuft deshalb mit niedriger Priorität.
func _lower_priority(pid: int) -> void:
	if OS.get_name() == "Windows":
		OS.create_process("powershell.exe", ["-NoProfile", "-WindowStyle", "Hidden", "-Command",
			"(Get-Process -Id %d).PriorityClass='BelowNormal'" % pid])
	else:
		OS.create_process("renice", ["-n", "10", "-p", str(pid)])


# ------------------------------------------------------------------ Zusammensetzen

func _write_pack() -> void:
	var all: Array = []
	# Reihenfolge: erst die umgewandelten (in Planreihenfolge), dann die übernommenen
	var seen := {}
	for a in _assets:
		all.append(a)
		seen[a["n"]] = true
	for c in _copy:
		if not seen.has(c["n"]):
			all.append(c)
	var entries: Array = []
	var sizes: Array = []
	for a in all:
		var fa := FileAccess.open(str(a["file"]), FileAccess.READ)
		if fa == null:
			continue
		sizes.append(fa.get_length())
		fa.close()
		entries.append(a)
	# Verzeichnis zweimal bauen: die Länge des Verzeichnisses verschiebt alle Anfänge
	var manifest := _manifest(entries, sizes, 0)
	var head := 12 + manifest.to_utf8_buffer().size()
	manifest = _manifest(entries, sizes, head)
	var text := manifest.to_utf8_buffer()
	if text.size() + 12 != head:
		manifest = _manifest(entries, sizes, 12 + text.size())
		text = manifest.to_utf8_buffer()
	var tmp := out_file + ".part"
	var f := FileAccess.open(tmp, FileAccess.WRITE)
	if f == null:
		state = "error"
		error = "write"
		return
	f.store_buffer("VGPACK".to_utf8_buffer())
	f.store_8(1)
	f.store_8(10)
	f.store_32(text.size())
	f.store_buffer(text)
	for e in entries:
		var src := FileAccess.open(str(e["file"]), FileAccess.READ)
		if src == null:
			continue
		while src.get_position() < src.get_length():
			f.store_buffer(src.get_buffer(1 << 20))
		src.close()
	f.close()
	_clear(_work)
	DirAccess.remove_absolute(out_file)
	if DirAccess.rename_absolute(tmp, out_file) != OK:
		state = "error"
		error = "rename"
		return
	_prune()
	state = "done"
	progress = 1.0


func _manifest(entries: Array, sizes: Array, head: int) -> String:
	var assets: Array = []
	var off := head
	for i in entries.size():
		var e: Dictionary = entries[i]
		assets.append({"n": e["n"], "o": off, "l": sizes[i], "m": e["m"]})
		off += int(sizes[i])
	return JSON.stringify({"v": 1, "src": src_fp, "assets": assets})


# ------------------------------------------------------------------ Kleinkram

func _mime(ext: String) -> String:
	match ext:
		"png": return "image/png"
		"jpg", "jpeg": return "image/jpeg"
		"webp": return "image/webp"
		"bmp": return "image/bmp"
		"ogg", "opus": return "audio/ogg"
		"wav": return "audio/wav"
		"mp3": return "audio/mpeg"
		"flac": return "audio/flac"
		"m4a", "aac": return "audio/mp4"
		"mp4", "mov": return "video/mp4"
		"webm": return "video/webm"
		"ogv": return "video/ogg"
	return "application/octet-stream"


func _touch(path: String) -> void:
	# Nur damit _prune() sieht, was zuletzt gebraucht wurde
	var f := FileAccess.open(path + ".used", FileAccess.WRITE)
	if f:
		f.store_string(str(Time.get_unix_time_from_system()))
		f.close()


## Älteste fertige Packs wegräumen, damit der Ordner nicht wächst.
func _prune() -> void:
	_touch(out_file)
	var list: Array = []
	for name in DirAccess.get_files_at(root()):
		if name.ends_with(".vgpack"):
			list.append(name)
	if list.size() <= KEEP:
		return
	list.sort_custom(func(a, b): return _used_at(a) > _used_at(b))
	for i in range(KEEP, list.size()):
		DirAccess.remove_absolute(root().path_join(list[i]))
		DirAccess.remove_absolute(root().path_join(list[i] + ".used"))


func _used_at(name: String) -> float:
	var p := root().path_join(name + ".used")
	if FileAccess.file_exists(p):
		return float(FileAccess.get_file_as_string(p))
	return 0.0


func _clear(dir: String) -> void:
	if not DirAccess.dir_exists_absolute(dir):
		return
	for f in DirAccess.get_files_at(dir):
		DirAccess.remove_absolute(dir.path_join(f))
	DirAccess.remove_absolute(dir)

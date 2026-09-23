extends RefCounted
## Video-Export auf diesem PC statt auf dem Server (braucht ffmpeg, siehe pack_web.gd).
##
## Der Server hat zwei Kerne und wandelt für jeden Export ein ganzes Video um. Hier liegt sowieso
## schon alles: das Pack, der Hintergrund und die fertigen Zeilen aus dem Spiel. Gibt es das
## Browser-Pack (pack_web.gd), ist das Video darin schon H.264 und wird nur noch durchgereicht,
## der Export dauert dann Sekunden.
##
## Benutzung (dub_hook.gd):
##   var e := ExportLocal.new()
##   e.start({...})
##   e.poll()      jeden Frame, bis state in ["done", "error"]

const SR := 44100

var state := ""                  # "" | run | done | error
var progress := 0.0
var error := ""
var out_file := ""

var _pid := -1
var _prog := ""                  # Datei, in die ffmpeg den Fortschritt schreibt
var _duration := 0.0
var _part := ""
var _work := ""
var _args: Array = []


## opts:
##   ffmpeg      Pfad zu ffmpeg
##   video       Videodatei (Original aus dem Pack oder schon umgewandelt)
##   copy_video  true = Video unverändert übernehmen (kommt aus dem Browser-Pack)
##   backing     Hintergrundmusik oder ""
##   lines       [{"file": Pfad, "times": [Sekunden]}]
##   duration    Länge des Videos in Sekunden (0 = ffmpeg entscheidet)
##   out         Zieldatei (.mp4)
##   work        Arbeitsordner (echter Pfad)
func start(opts: Dictionary) -> void:
	# ffmpeg kennt kein user://: alle Pfade in echte umrechnen
	out_file = _real(str(opts.get("out", "")))
	_work = _real(str(opts.get("work", "")))
	_duration = float(opts.get("duration", 0.0))
	_part = out_file + ".part"
	var video := _real(str(opts.get("video", "")))
	if video == "" or not FileAccess.file_exists(video):
		state = "error"
		error = "video"
		return
	DirAccess.make_dir_recursive_absolute(_work)
	DirAccess.make_dir_recursive_absolute(out_file.get_base_dir())
	if FileAccess.file_exists(_part):
		DirAccess.remove_absolute(_part)

	var args := ["-hide_banner", "-nostdin", "-y", "-threads", "2"]
	_prog = _work.path_join("progress.txt")
	args.append_array(["-progress", _prog, "-nostats", "-i", video])

	# Tonspuren: Hintergrund und jede Zeile an ihren Zeitpunkten
	var graph: Array = []
	var mixed: Array = []
	var index := 1
	var backing := _real(str(opts.get("backing", "")))
	if backing != "" and FileAccess.file_exists(backing):
		args.append_array(["-i", backing])
		graph.append("[%d:a]%s[bg]" % [index, _fmt()])
		mixed.append("[bg]")
		index += 1
	for line in opts.get("lines", []):
		var file := _real(str(line.get("file", "")))
		var times: Array = line.get("times", [])
		if file == "" or times.is_empty() or not FileAccess.file_exists(file):
			continue
		args.append_array(["-i", file])
		var tag := "s%d" % index
		var outs := ""
		for k in times.size():
			outs += "[%s_%d]" % [tag, k]
		if times.size() > 1:
			graph.append("[%d:a]%s,asplit=%d%s" % [index, _fmt(), times.size(), outs])
		else:
			graph.append("[%d:a]%s%s" % [index, _fmt(), outs])
		for k in times.size():
			var ms := int(round(maxf(0.0, float(times[k])) * 1000.0))
			graph.append("[%s_%d]adelay=delays=%d:all=1[%s_d%d]" % [tag, k, ms, tag, k])
			mixed.append("[%s_d%d]" % [tag, k])
		index += 1

	if mixed.is_empty():
		# Nichts zu mischen (nur Originalton im Video gibt es hier nicht): stille Tonspur
		args.append_array(["-f", "lavfi", "-i", "anullsrc=r=%d:cl=stereo" % SR])
		graph.append("[%d:a]anull[mix]" % index)
	else:
		# normalize=0: die Zeilen sollen nicht leiser werden, nur weil es viele sind.
		# alimiter fängt ab, wenn es dadurch zu laut wird.
		graph.append("%samix=inputs=%d:normalize=0:duration=longest,alimiter=limit=0.98[mix]"
			% ["".join(PackedStringArray(mixed)), mixed.size()])

	var script := _work.path_join("filter.txt")
	var f := FileAccess.open(script, FileAccess.WRITE)
	if f == null:
		state = "error"
		error = "work"
		return
	f.store_string(";\n".join(PackedStringArray(graph)))
	f.close()

	args.append_array(["-filter_complex_script", script, "-map", "0:v:0", "-map", "[mix]"])
	if bool(opts.get("copy_video", false)):
		args.append_array(["-c:v", "copy"])
	else:
		args.append_array(["-vf", "scale=-2:'trunc(min(1080,ih)/2)*2',format=yuv420p",
			"-c:v", "libx264", "-preset", "veryfast", "-crf", "23"])
	args.append_array(["-c:a", "aac", "-b:a", "192k", "-ac", "2"])
	if _duration > 0.0:
		args.append_array(["-t", "%.3f" % _duration])   # genau so lang wie das Video
	# .part als Endung kennt ffmpeg nicht, deshalb das Format ausdrücklich nennen
	args.append_array(["-movflags", "+faststart", "-f", "mp4", _part])

	_args = args
	_pid = OS.create_process(str(opts.get("ffmpeg", "ffmpeg")), args)
	if _pid <= 0:
		state = "error"
		error = "start"
		return
	state = "run"
	progress = 0.0


func poll() -> void:
	if state != "run":
		return
	_read_progress()
	if _pid > 0 and OS.is_process_running(_pid):
		return
	var code := OS.get_process_exit_code(_pid) if _pid > 0 else -1
	_pid = -1
	if code != 0 or not FileAccess.file_exists(_part):
		state = "error"
		error = "ffmpeg %d" % code
		_keep_for_report(code)
		return
	if FileAccess.file_exists(out_file):
		OS.move_to_trash(ProjectSettings.globalize_path(out_file))
	if DirAccess.rename_absolute(_part, out_file) != OK:
		state = "error"
		error = "rename"
		_cleanup()
		return
	progress = 1.0
	state = "done"
	_cleanup()


func cancel() -> void:
	if _pid > 0 and OS.is_process_running(_pid):
		OS.kill(_pid)
	_pid = -1
	if _part != "" and FileAccess.file_exists(_part):
		DirAccess.remove_absolute(_part)
	_cleanup()
	state = ""


## ffmpeg schreibt den Stand fortlaufend in eine Datei, die letzte Zeile zählt.
func _read_progress() -> void:
	if _duration <= 0.0 or not FileAccess.file_exists(_prog):
		return
	var text := FileAccess.get_file_as_string(_prog)
	var at := text.rfind("out_time_us=")
	if at < 0:
		at = text.rfind("out_time_ms=")
	if at < 0:
		return
	var line := text.substr(at, 40).split("\n")[0]
	var us := float(line.split("=")[-1])
	if us > 0.0:
		progress = clampf(us / 1e6 / _duration, 0.0, 1.0)


## Fehlersuche: den Aufruf neben die Arbeitsdateien legen, dann lässt er sich von Hand nachstellen.
func _keep_for_report(code: int) -> void:
	var f := FileAccess.open(_work.path_join("last_error.txt"), FileAccess.WRITE)
	if f:
		f.store_line("ffmpeg %d" % code)
		f.store_line(" ".join(PackedStringArray(_args)))
		f.close()


func _cleanup() -> void:
	for name in ["progress.txt", "filter.txt"]:
		var p := _work.path_join(name)
		if FileAccess.file_exists(p):
			DirAccess.remove_absolute(p)


static func _real(path: String) -> String:
	return ProjectSettings.globalize_path(path) if path != "" else ""


static func _fmt() -> String:
	return "aformat=sample_rates=%d:channel_layouts=stereo" % SR

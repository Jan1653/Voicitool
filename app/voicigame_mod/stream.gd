extends Node
## Live-Bild und Ton des Spiels an zuschauende Mitspieler (Handy, Browser, andere PCs).
## Läuft nur, solange jemand zuschaut (Server meldet „watchers"). Format siehe server/src/stream.js.
##   Bild   alle 1/FPS s ein Bildschirmfoto, verkleinert und als JPEG (Umwandlung im Hintergrund-Thread)
##   Ton    Ausgang des Spiels (Kanal Master), gemischt auf Mono, AUDIO_RATE, alle ~100 ms ein Paket

const FRAME_IMAGE := 1
const FRAME_AUDIO := 2
const AUDIO_RATE := 24000

var bridge: Node
var fps := 8.0
var width := 640
var quality := 0.6

var _next_shot := 0
var _encoding := false
var _task := -1                  # laufende Umwandlung im Hintergrund (muss abgeholt werden, sonst bleibt sie im Speicher)
var _capture: AudioEffectCapture
var _audio_acc := PackedByteArray()
var _step := 1


func _ready() -> void:
	var cfg := ConfigFile.new()
	if cfg.load("user://voicigame.cfg") == OK:
		fps = clampf(float(cfg.get_value("stream", "fps", fps)), 1.0, 20.0)
		width = clampi(int(cfg.get_value("stream", "width", width)), 320, 1280)
		quality = clampf(float(cfg.get_value("stream", "quality", quality)), 0.3, 0.9)


func _watching() -> bool:
	return bridge.connected and int(bridge.state.get("watchers", 0)) > 0


func _process(_delta: float) -> void:
	if not _watching():
		_stop_audio()
		return
	_pump_audio()
	var now := Time.get_ticks_msec()
	if now >= _next_shot and not _encoding:
		_next_shot = now + int(1000.0 / fps)
		_grab.call_deferred()


# ------------------------------------------------------------------
# Bild
# ------------------------------------------------------------------

func _grab() -> void:
	_encoding = true
	await RenderingServer.frame_post_draw
	var tex := get_viewport().get_texture()
	var img := tex.get_image() if tex else null
	if img == null or img.is_empty():
		_encoding = false
		return
	_task = WorkerThreadPool.add_task(_encode.bind(img), false, "voicigame stream")


func _encode(img: Image) -> void:
	var w := mini(width, img.get_width())
	var h := int(round(img.get_height() * float(w) / img.get_width()))
	if img.is_compressed():
		img.decompress()
	img.resize(w, h, Image.INTERPOLATE_BILINEAR)
	var jpg := img.save_jpg_to_buffer(quality)
	_send_image.call_deferred(jpg)


func _send_image(jpg: PackedByteArray) -> void:
	# Aufgabe abholen: erst dann gibt Godot sie samt Bildschirmfoto wieder frei
	if _task >= 0:
		WorkerThreadPool.wait_for_task_completion(_task)
		_task = -1
	_encoding = false
	if jpg.is_empty() or not _watching():
		return
	var out := PackedByteArray([FRAME_IMAGE])
	out.append_array(jpg)
	bridge.send_binary(out)


# ------------------------------------------------------------------
# Ton
# ------------------------------------------------------------------

func _start_audio() -> void:
	if _capture:
		return
	_capture = AudioEffectCapture.new()
	_capture.buffer_length = 0.5
	AudioServer.add_bus_effect(0, _capture)
	_step = maxi(1, int(round(AudioServer.get_mix_rate() / AUDIO_RATE)))
	_audio_acc = PackedByteArray()


func _stop_audio() -> void:
	if _capture == null:
		return
	for i in AudioServer.get_bus_effect_count(0):
		if AudioServer.get_bus_effect(0, i) == _capture:
			AudioServer.remove_bus_effect(0, i)
			break
	_capture = null


func _pump_audio() -> void:
	_start_audio()
	var n := _capture.get_frames_available()
	if n <= 0:
		return
	var buf := _capture.get_buffer(n)
	# Mono mischen und auf AUDIO_RATE herunterrechnen (Mittelwert über _step Samples)
	var count := buf.size() / _step
	var start := _audio_acc.size()
	_audio_acc.resize(start + count * 2)
	for i in count:
		var acc := 0.0
		for k in _step:
			var v := buf[i * _step + k]
			acc += v.x + v.y
		_audio_acc.encode_s16(start + i * 2, int(clampf(acc / (2.0 * _step), -1.0, 1.0) * 32767.0))
	var rate := int(AudioServer.get_mix_rate() / _step)
	if _audio_acc.size() >= rate / 10 * 2:
		var out := PackedByteArray([FRAME_AUDIO, 0, 0, 0, 0])
		out.encode_u32(1, rate)
		out.append_array(_audio_acc)
		bridge.send_binary(out)
		_audio_acc = PackedByteArray()

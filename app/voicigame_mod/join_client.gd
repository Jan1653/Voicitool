extends Node
## PC-Mitspieler: tritt einem voicigame-Raum bei wie ein Handy (Rolle „phone").
## Lädt die Clips, nimmt mit dem im Spiel eingestellten Mikrofon auf und schickt die Aufnahme.
## Keine Oberfläche, die macht join_screen.gd.

signal joined(player_id: String)
signal state_changed(state: Dictionary)
signal round_started(round: Dictionary)
signal scores_received(scores: Array)
signal show_ended(ranking: Array)
signal connection_changed(connected: bool)
signal error_received(code: String, message: String)
signal kicked
signal clip_ready(clip_id: String, stream: AudioStream)
signal upload_done(round_id: String, ok: bool, message: String)
signal live_frame(data: PackedByteArray)   # Live-Bild/Ton vom Host, Format siehe server/src/stream.js
signal message_received(msg: Dictionary)   # jede Nachricht vom Server (Dub-Modus im eigenen Spiel hört hier mit)

const WavUtil = preload("wav_util.gd")
const Bridge = preload("bridge.gd")
const CONFIG := "user://voicigame.cfg"
const BUS := "VoicigameJoin"

var server_url := ""
var mod_version := ""            # für die Nutzungsstatistik des Servers, setzt join_screen.gd
var code := ""
var player_name := ""
var token := ""
var player_id := ""
var state: Dictionary = {}
var connected := false
# Wie bridge.gd, damit dub_hook.gd auch hier mitspielen kann (Dub-Modus im eigenen Spiel)
var room_code := ""
var join_url := ""
var host_key := ""               # bleibt leer: dieser PC ist Mitspieler, nicht Host

var _ws := WebSocketPeer.new()
var _active := false
var _reconnect_at := 0
var _clips := {}                 # clip_id -> AudioStream
var _http_clip: HTTPRequest
var _http_up: HTTPRequest
var _clip_queue: Array = []
var _clip_busy := ""
var _up_round := ""
var _watch := false
var _last_round := ""            # zuletzt gemeldete Gameshow-Runde

# Mikrofon
var _mic: AudioStreamPlayer
var _rec: AudioEffectRecord
var _cap: AudioEffectCapture
var _prev_device := ""
var _switched := false
var level := 0.0                 # 0..1, Pegel während der Aufnahme


## Nur im Testlauf: Schritte ins Spielprotokoll.
static func _dbg(msg: String) -> void:
	if OS.get_environment("VOICIGAME_TEST") != "":
		print("Voicigame | Client: " + msg)


func _ready() -> void:
	_http_clip = HTTPRequest.new()
	_http_clip.request_completed.connect(_on_clip_done)
	add_child(_http_clip)
	_http_up = HTTPRequest.new()
	_http_up.request_completed.connect(_on_upload_done)
	add_child(_http_up)


# ------------------------------------------------------------------
# Verbindung
# ------------------------------------------------------------------

func join(room_code: String, name: String) -> void:
	code = room_code.strip_edges().to_upper()
	self.room_code = code
	join_url = "%s/?r=%s" % [server_url, code]
	player_name = name.strip_edges().left(24)
	var cfg := ConfigFile.new()
	cfg.load(CONFIG)
	token = str(cfg.get_value("join", "token", "")) if str(cfg.get_value("join", "code", "")) == code else ""
	_save_cfg({"name": player_name})
	_last_round = ""
	_active = true
	_connect()


## Werte in [join] speichern. Ist die Datei da, aber nicht lesbar, nichts überschreiben.
func _save_cfg(values: Dictionary) -> void:
	var cfg := ConfigFile.new()
	var err := cfg.load(CONFIG)
	if err != OK and FileAccess.file_exists(CONFIG):
		push_warning("Voicigame: %s ist nicht lesbar, speichere nichts" % CONFIG)
		return
	for k in values:
		cfg.set_value("join", k, values[k])
	cfg.save(CONFIG)


func leave() -> void:
	_active = false
	if _ws.get_ready_state() == WebSocketPeer.STATE_OPEN:
		# Abmelden, sonst bleibt man als getrennter Spieler im Raum und wird ins nächste Spiel übernommen
		_send({"type": "leave"})
		_ws.close(1000, "leave")
	connected = false
	mic_off()


## Live-Bild vom Host an oder aus.
func watch(on: bool) -> void:
	if on == _watch:
		return
	_watch = on
	_send({"type": "watch", "on": on})


func _connect() -> void:
	_ws = WebSocketPeer.new()
	_ws.inbound_buffer_size = 1 << 22
	_ws.connect_to_url(server_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws")


func _process(_delta: float) -> void:
	if _cap:
		_read_level()
	if not _active:
		return
	_ws.poll()
	match _ws.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			if not connected:
				connected = true
				var hello := {"type": "hello", "role": "phone", "code": code, "name": player_name, "token": token if token != "" else null}
				hello.merge(Bridge.client_info(mod_version))
				_send(hello)
				if _watch:
					_send({"type": "watch", "on": true})
				connection_changed.emit(true)
			while _ws.get_available_packet_count() > 0:
				var pkt := _ws.get_packet()
				if not _ws.was_string_packet():
					live_frame.emit(pkt)
					continue
				var msg = JSON.parse_string(pkt.get_string_from_utf8())
				if msg is Dictionary:
					_handle(msg)
		WebSocketPeer.STATE_CLOSED:
			if connected:
				connected = false
				connection_changed.emit(false)
			if Time.get_ticks_msec() >= _reconnect_at:
				_reconnect_at = Time.get_ticks_msec() + 2000
				_connect()


func _send(msg: Dictionary) -> void:
	if _ws.get_ready_state() == WebSocketPeer.STATE_OPEN:
		_ws.send_text(JSON.stringify(msg))


## Für dub_hook.gd (wie bridge.gd): im Raum?
func has_room() -> bool:
	return _active and code != ""


## Mitspieler aus dem Browser oder mit eigenem Spiel (alle außer den Spielern am Host-PC).
func web_players() -> Array:
	return state.get("players", []).filter(func(p): return str(p.get("kind", "")) == "phone")


func qr_url() -> String:
	return "%s/api/rooms/%s/qr.png" % [server_url, code]


func _handle(msg: Dictionary) -> void:
	_dbg("Nachricht " + str(msg.get("type", "")))
	message_received.emit(msg)
	match str(msg.get("type", "")):
		"welcome":
			player_id = str(msg.playerId)
			token = str(msg.token)
			_save_cfg({"code": code, "token": token})
			joined.emit(player_id)
		"state":
			state = msg.state if msg.state is Dictionary else {}
			_preload_clips()
			# Eine Runde, die während eines Verbindungsabbruchs begann, steht nur im Zustand
			var show = state.get("show")
			var rnd = show.get("round") if show is Dictionary else null
			if rnd is Dictionary:
				_new_round(rnd)
			state_changed.emit(state)
		"show.round":
			if msg.round is Dictionary:
				_new_round(msg.round)
		"show.scores":
			scores_received.emit(msg.get("scores", []))
		"show.end":
			show_ended.emit(msg.get("ranking", []))
		"kicked":
			_active = false
			_forget_token()
			kicked.emit()
		"error":
			var ecode := str(msg.get("code", ""))
			if ecode == "room_not_found":
				_active = false
				_forget_token()
			elif ecode == "room_full":
				_active = false   # sonst verbindet es alle 2 s neu und wird wieder abgewiesen
			error_received.emit(ecode, str(msg.get("message", "")))


func _new_round(rnd: Dictionary) -> void:
	var rid := str(rnd.get("roundId", ""))
	if rid == "" or rid == _last_round:
		return
	_last_round = rid
	want_clip(str(rnd.get("clipId", "")), true)
	round_started.emit(rnd)


func _forget_token() -> void:
	token = ""
	_save_cfg({"token": ""})


# ------------------------------------------------------------------
# Clips
# ------------------------------------------------------------------

func clip(clip_id: String) -> AudioStream:
	return _clips.get(clip_id)


func _preload_clips() -> void:
	for c in state.get("clips", []):
		if c is Dictionary and c.get("available", false):
			want_clip(str(c.id))


func want_clip(clip_id: String, urgent := false) -> void:
	if _clips.has(clip_id) or clip_id == _clip_busy:
		return
	_clip_queue.erase(clip_id)
	if urgent:
		_clip_queue.push_front(clip_id)
	else:
		_clip_queue.append(clip_id)
	_next_clip()


func _next_clip() -> void:
	if _clip_busy != "" or _clip_queue.is_empty() or token == "":
		return
	_clip_busy = _clip_queue.pop_front()
	_dbg("lade Clip " + _clip_busy)
	var url := "%s/api/rooms/%s/clips/%s?t=%s" % [server_url, code, _clip_busy.uri_encode(), token]
	if _http_clip.request(url) != OK:
		_clip_busy = ""


func _on_clip_done(_result: int, status: int, headers: PackedStringArray, body: PackedByteArray) -> void:
	var id := _clip_busy
	_clip_busy = ""
	_dbg("Clip %s da: %d, %d Bytes" % [id, status, body.size()])
	if status == 200:
		var s := _stream_from(body, headers)
		_dbg("Clip %s gelesen: %s" % [id, s != null])
		if s:
			_clips[id] = s
			clip_ready.emit(id, s)
		else:
			push_warning("Voicigame: Clip %s nicht lesbar" % id)
	else:
		push_warning("Voicigame: Clip %s nicht geladen (%d)" % [id, status])
	_next_clip.call_deferred()


static func _stream_from(body: PackedByteArray, headers: PackedStringArray) -> AudioStream:
	var type := ""
	for h in headers:
		if h.to_lower().begins_with("content-type:"):
			type = h.substr(13).strip_edges().to_lower()
	if type.contains("mpeg") or type.contains("mp3"):
		var mp3 := AudioStreamMP3.new()
		mp3.data = body
		return mp3
	if type.contains("ogg"):
		return AudioStreamOggVorbis.load_from_buffer(body)
	return WavUtil.from_wav_bytes(body)


# ------------------------------------------------------------------
# Aufnahme mit dem Mikrofon aus den Spieleinstellungen
# ------------------------------------------------------------------

func _ensure_bus() -> void:
	if _mic:
		return
	var idx := AudioServer.get_bus_index(BUS)
	if idx < 0:
		idx = AudioServer.bus_count
		AudioServer.add_bus(idx)
		AudioServer.set_bus_name(idx, BUS)
		AudioServer.set_bus_send(idx, "Master")
		# nicht auf den Lautsprechern hörbar, die Effekte davor laufen trotzdem
		AudioServer.set_bus_volume_db(idx, -80.0)
		_rec = AudioEffectRecord.new()
		_rec.format = AudioStreamWAV.FORMAT_16_BITS
		AudioServer.add_bus_effect(idx, _rec)
		_cap = AudioEffectCapture.new()
		_cap.buffer_length = 0.2
		AudioServer.add_bus_effect(idx, _cap)
	else:
		for i in AudioServer.get_bus_effect_count(idx):
			var e = AudioServer.get_bus_effect(idx, i)
			if e is AudioEffectRecord:
				_rec = e
			elif e is AudioEffectCapture:
				_cap = e
	_mic = AudioStreamPlayer.new()
	_mic.stream = AudioStreamMicrophone.new()
	_mic.bus = BUS
	add_child(_mic)


## Mikrofon an (bleibt an, solange man im Raum ist; zeigt den Pegel schon vor der Aufnahme).
## Nur umschalten, wenn im Spiel ein anderes Mikrofon eingestellt ist: jeder Wechsel startet
## den Toneingang neu, das kostet Zeit und kann bei manchen Treibern hängen.
func mic_on() -> void:
	_ensure_bus()
	if _mic.playing or OS.get_environment("VG_NO_MIC") != "":   # VG_NO_MIC: nur für Tests
		return
	_prev_device = AudioServer.input_device
	_switched = false
	var profile = get_node_or_null("/root/Profile")
	var dev := str(profile.get("audio_device_in")) if profile else ""
	if dev != "" and dev != _prev_device and dev in AudioServer.get_input_device_list():
		AudioServer.input_device = dev
		_switched = true
	_mic.play()


func mic_off() -> void:
	if _mic == null or not _mic.playing:
		return
	if _rec.is_recording_active():
		_rec.set_recording_active(false)
	_mic.stop()
	level = 0.0
	if _switched:
		AudioServer.input_device = _prev_device
		_switched = false


func start_recording() -> void:
	mic_on()
	_rec.set_recording_active(true)


## Beendet die Aufnahme, das Mikrofon bleibt an. -> Aufnahme oder null
func stop_recording() -> AudioStreamWAV:
	if _rec == null or not _rec.is_recording_active():
		return null
	_rec.set_recording_active(false)
	return _rec.get_recording()


func _read_level() -> void:
	var n := _cap.get_frames_available()
	if n <= 0:
		return
	var buf := _cap.get_buffer(n)
	var peak := 0.0
	for f in buf:
		peak = maxf(peak, maxf(absf(f.x), absf(f.y)))
	level = maxf(peak, level * 0.85)


## Aufnahme als WAV zur Runde schicken. pad: Stille vorneweg, damit die Stimme im Spiel zum Clip passt.
func upload_take(round_id: String, take: AudioStreamWAV, pad: float) -> void:
	_up_round = round_id
	var bytes := WavUtil.to_wav_bytes(take, pad)
	_dbg("sende Aufnahme, %d Bytes" % bytes.size())
	var url := "%s/api/rooms/%s/rounds/%s/recording?t=%s" % [server_url, code, round_id, token]
	var err := _http_up.request_raw(url, PackedStringArray(["Content-Type: audio/wav"]), HTTPClient.METHOD_POST, bytes)
	if err != OK:
		upload_done.emit(round_id, false, "")


func _on_upload_done(_result: int, status: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	_dbg("Aufnahme gesendet: %d" % status)
	var msg := ""
	if status == 409:
		msg = "Die Runde ist schon vorbei."
	elif status != 200:
		msg = "Senden fehlgeschlagen"
		push_warning("Voicigame: Upload fehlgeschlagen (%d) %s" % [status, body.get_string_from_utf8().left(200)])
	upload_done.emit(_up_round, status == 200, msg)

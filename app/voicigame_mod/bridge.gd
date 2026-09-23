extends Node
## Verbindung zum voicigame-Server: Raum anlegen, Clips hochladen, Runden starten,
## Aufnahmen der Web-Spieler abholen, Punkte zurückmelden. Keine Spiellogik.

signal room_ready(code: String, join_url: String)
signal state_changed(state: Dictionary)
signal connection_changed(connected: bool)
signal recording_received(round_id: String, player_id: String, stream: AudioStreamWAV)
signal recording_failed(round_id: String, player_id: String)
signal show_recording(round_id: String, player_id: String)   # neue Handy-Aufnahme liegt auf dem Server
signal room_lost                                             # Raum gibt es auf dem Server nicht mehr
signal queued(position: int)                                 # Server voll: Platz in der Warteschlange, es geht von selbst weiter
signal clip_uploaded(clip_id: String, ok: bool)
signal error_received(code: String, message: String)
signal message_received(msg: Dictionary)   # jede Nachricht vom Server (Dub-Modus hört hier mit)
signal notice_changed                      # Hinweis vom Server (Wartung, Mod zu alt), siehe notice

const WavUtil = preload("wav_util.gd")
const I18n = preload("i18n.gd")
const DEFAULT_SERVER := "https://voicigame.duckdns.org"

var server_url := DEFAULT_SERVER
var mod_version := ""          # setzt main.gd, geht in die Nutzungsstatistik des Servers
var room_code := ""
var host_key := ""
var join_url := ""
var state: Dictionary = {}
var connected := false
## Hinweise des Servers: Code -> {"level": "info|update|block", "text": ...}. Anzeige siehe notice_text().
var notice: Dictionary = {}

var _ws := WebSocketPeer.new()
var _pending: Array = []
var _reconnect_at := 0
var _http: HTTPRequest
var _jobs: Array = []
var _job = null
var _queue_ticket := ""          # Wartenummer, wenn der Server gerade voll ist
var _queue_retry_at := 0


func _init() -> void:
	_http = HTTPRequest.new()
	_http.timeout = 60.0
	_http.request_completed.connect(_on_http_done)
	add_child(_http)


## Grobe Angaben für die Nutzungsstatistik des Servers: Mod-Version, Version des Spiels
## und Betriebssystem. Keine Namen, keine Adressen, der Server zählt nur Tagessummen.
static func client_info(version: String) -> Dictionary:
	return {
		"client": "game",
		"version": version,
		"game": str(ProjectSettings.get_setting("application/config/version", "")),
		"os": OS.get_name(),
	}


func has_room() -> bool:
	return room_code != "" and host_key != ""


func web_players() -> Array:
	var out: Array = []
	for p in state.get("players", []):
		if p.get("kind", "") == "phone":
			out.append(p)
	return out


# ------------------------------------------------------------------
# Raum
# ------------------------------------------------------------------

func create_room() -> void:
	close_room()
	_request_room()


func _request_room() -> void:
	var path := "/api/rooms" + ("?ticket=" + _queue_ticket.uri_encode() if _queue_ticket != "" else "")
	var body := JSON.stringify(client_info(mod_version)).to_utf8_buffer()
	_http_job(HTTPClient.METHOD_POST, path, ["Content-Type: application/json"], body, _on_room_created)


func close_room() -> void:
	if has_room():
		_send({"type": "game.end"})
	_ws.close()
	room_code = ""
	host_key = ""
	join_url = ""
	state = {}
	connected = false
	_pending.clear()
	_queue_ticket = ""
	_queue_retry_at = 0


func qr_url() -> String:
	return "%s/api/rooms/%s/qr.png" % [server_url, room_code]


func kick(player_id: String) -> void:
	_send({"type": "player.kick", "playerId": player_id})


func back_to_lobby() -> void:
	_send({"type": "game.reset"})


func _on_room_created(code: int, body: PackedByteArray) -> void:
	if code == 503:
		# Server voll: in der Warteschlange bleiben und in ein paar Sekunden mit derselben Wartenummer nachfragen
		var busy = JSON.parse_string(body.get_string_from_utf8()) if body.size() else null
		var q = busy.get("queue") if busy is Dictionary else null
		if q is Dictionary:
			_queue_ticket = str(q.get("ticket", ""))
			_queue_retry_at = Time.get_ticks_msec() + 4000
			queued.emit(int(q.get("position", 0)))
			return
	_queue_ticket = ""
	if code != 200:
		# Meldung des Servers (z. B. „Zu viele neue Räume …“) bevorzugen, die Anzeige übersetzt sie
		var info = JSON.parse_string(body.get_string_from_utf8()) if body.size() else null
		var msg := str(info.get("message", "")) if info is Dictionary else ""
		if msg == "":
			msg = "%s (%s)" % [I18n.t("Der Raum konnte nicht erstellt werden."), server_url]
		error_received.emit("create_failed", msg)
		return
	var d = JSON.parse_string(body.get_string_from_utf8())
	if not d is Dictionary:
		error_received.emit("create_failed", "%s (%s)" % [I18n.t("Der Raum konnte nicht erstellt werden."), server_url])
		return
	room_code = str(d.code)
	host_key = str(d.hostKey)
	join_url = str(d.joinUrl)
	_connect_ws()
	room_ready.emit(room_code, join_url)


# ------------------------------------------------------------------
# Gameshow
# ------------------------------------------------------------------

## Liste der Clips (id, title, character, order), damit die Handys Namen anzeigen können.
func set_clips(list: Array) -> void:
	_send({"type": "clips.set", "clips": list})


## Clip für die Handys bereitstellen. Die Handys laden ihn im Hintergrund vor.
func upload_clip(clip_id: String, path: String, title := "", duration := 0.0) -> void:
	var bytes := FileAccess.get_file_as_bytes(path)
	if bytes.is_empty():
		push_warning("Voicigame: Clip nicht lesbar: %s" % path)
		return
	# Der Upload legt den Clip auf dem Server selbst an, die Handys laden ihn danach vor
	var headers := ["Content-Type: " + _mime_for(path), "X-Host-Key: " + host_key]
	_http_job(HTTPClient.METHOD_PUT, "/api/rooms/%s/clips/%s" % [room_code, clip_id.uri_encode()], headers, bytes, _on_upload_done.bind(clip_id))


func _on_upload_done(code: int, _body: PackedByteArray, clip_id: String) -> void:
	if code != 200:
		push_warning("Voicigame: Upload von Clip %s fehlgeschlagen (%d)" % [clip_id, code])
	clip_uploaded.emit(clip_id, code == 200)


## Runde starten: die Handys spielen den Clip ab und nehmen gleichzeitig auf.
## lead_in: so viele Sekunden nach Aufnahmebeginn startet der Clip (wie im Spiel).
func start_round(index: int, total: int, clip_id: String, seconds: float, lead_in: float, recorders: Array = []) -> void:
	_send({"type": "show.round", "index": index, "total": total, "clipId": clip_id, "seconds": seconds,
		"countdown": 3, "leadIn": lead_in, "recorders": recorders})


func set_status(text: String) -> void:
	_send({"type": "show.status", "text": text})


## scores: [{playerId, name, score, total, web}]
func send_scores(index: int, scores: Array) -> void:
	_send({"type": "show.scores", "index": index, "scores": scores})


func end_show(ranking: Array) -> void:
	_send({"type": "show.end", "ranking": ranking})


# ------------------------------------------------------------------
# WebSocket
# ------------------------------------------------------------------

func _connect_ws() -> void:
	var ws_url := server_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
	_ws = WebSocketPeer.new()
	_ws.inbound_buffer_size = 1 << 20
	_ws.outbound_buffer_size = 1 << 22   # Platz für Live-Bilder (stream.gd)
	_ws.connect_to_url(ws_url)


func _process(_delta: float) -> void:
	if _queue_retry_at > 0 and Time.get_ticks_msec() >= _queue_retry_at:
		_queue_retry_at = 0
		_request_room()
	if not has_room():
		return
	_ws.poll()
	match _ws.get_ready_state():
		WebSocketPeer.STATE_OPEN:
			if not connected:
				connected = true
				var hello := {"type": "hello", "role": "host", "code": room_code, "key": host_key}
				hello.merge(client_info(mod_version))
				_ws.send_text(JSON.stringify(hello))
				for m in _pending:
					_ws.send_text(m)
				_pending.clear()
				connection_changed.emit(true)
			while _ws.get_available_packet_count() > 0:
				var msg = JSON.parse_string(_ws.get_packet().get_string_from_utf8())
				if msg is Dictionary:
					_handle(msg)
		WebSocketPeer.STATE_CLOSED:
			if connected:
				connected = false
				connection_changed.emit(false)
			# Echte Zeit statt delta: während das Spiel auf eine Aufnahme wartet, steht die Spielzeit still
			if Time.get_ticks_msec() >= _reconnect_at:
				_reconnect_at = Time.get_ticks_msec() + 2000
				_connect_ws()


## Binärrahmen (Live-Bild/Ton). Staut sich die Leitung, wird der Rahmen ausgelassen statt gepuffert.
func send_binary(bytes: PackedByteArray) -> void:
	if not connected or _ws.get_ready_state() != WebSocketPeer.STATE_OPEN:
		return
	if _ws.get_current_outbound_buffered_amount() + bytes.size() > (1 << 21):
		return
	_ws.send(bytes, WebSocketPeer.WRITE_MODE_BINARY)


func _send(msg: Dictionary) -> void:
	var text := JSON.stringify(msg)
	if connected:
		_ws.send_text(text)
	else:
		_pending.append(text)


func _handle(msg: Dictionary) -> void:
	message_received.emit(msg)
	match str(msg.get("type", "")):
		"state":
			state = msg.state if msg.state is Dictionary else {}
			state_changed.emit(state)
		"show.recording":
			show_recording.emit(str(msg.get("roundId", "")), str(msg.get("playerId", "")))
		"notice":
			var nc := str(msg.get("code", ""))
			if nc != "":
				notice[nc] = {"level": str(msg.get("level", "info")), "text": str(msg.get("text", "")),
					"need": str(msg.get("need", "")), "at": Time.get_ticks_msec()}
				notice_changed.emit()
		"error":
			var code := str(msg.get("code", ""))
			if code == "room_not_found" and has_room():
				# Server neu gestartet oder Raum aufgeräumt: nicht an einem toten Raum hängen bleiben
				push_warning("Voicigame: Raum %s gibt es auf dem Server nicht mehr" % room_code)
				close_room()
				room_lost.emit()
				return
			error_received.emit(code, str(msg.get("message", "")))


## Aufnahme eines Handys zur Gameshow-Runde laden (vor alle wartenden Clip-Uploads, das Spiel braucht sie gleich).
func download_show_recording(round_id: String, player_id: String) -> void:
	var path := "/api/rooms/%s/rounds/%s/recording/%s" % [room_code, round_id.uri_encode(), player_id.uri_encode()]
	_http_job(HTTPClient.METHOD_GET, path, ["X-Host-Key: " + host_key], PackedByteArray(),
		_on_recording_downloaded.bind(round_id, player_id), true)


func _on_recording_downloaded(code: int, body: PackedByteArray, round_id: String, player_id: String) -> void:
	var stream := WavUtil.from_wav_bytes(body) if code == 200 else null
	if stream == null:
		push_warning("Voicigame: Aufnahme von %s nicht geladen (%d)" % [player_id, code])
		recording_failed.emit(round_id, player_id)
		return
	recording_received.emit(round_id, player_id, stream)


# ------------------------------------------------------------------
# HTTP-Warteschlange (ein HTTPRequest, Aufträge nacheinander)
# ------------------------------------------------------------------

func _http_job(method: int, path: String, headers: Array, body: PackedByteArray, cb: Callable, urgent := false) -> void:
	var job := {"method": method, "url": server_url + path, "headers": PackedStringArray(headers), "body": body, "cb": cb}
	if urgent:
		_jobs.push_front(job)
	else:
		_jobs.append(job)
	_next_job()


func _next_job() -> void:
	if _job != null or _jobs.is_empty():
		return
	if not _http.is_inside_tree():
		_next_job.call_deferred()
		return
	_job = _jobs.pop_front()
	var err := _http.request_raw(_job.url, _job.headers, _job.method, _job.body)
	if err != OK:
		var cb: Callable = _job.cb
		_job = null
		cb.call(0, PackedByteArray())
		_next_job()


func _on_http_done(_result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var cb: Callable = _job.cb
	_job = null
	if cb.is_valid():
		cb.call(code, body)
	_next_job()


static func _mime_for(path: String) -> String:
	match path.get_extension().to_lower():
		"wav": return "audio/wav"
		"ogg": return "audio/ogg"
		"mp3": return "audio/mpeg"
		"flac": return "audio/flac"
		"m4a": return "audio/mp4"
	return "application/octet-stream"


## Text für die Hinweiszeile („Wartung“, „Mod ist zu alt“), "" = kein Hinweis.
func notice_text() -> String:
	return notice_text_of(notice)


## Wie notice_text(), für alle, die Hinweise sammeln (auch join_client.gd).
## Über Voicitool installiert aktualisiert Voicitool den Mod, sonst holt der Mod sich das Update selbst.
static func notice_text_of(notes: Dictionary) -> String:
	var out: Array = []
	var mod = notes.get("mod_old")
	if mod is Dictionary:
		var by_voicitool := FileAccess.file_exists(I18n.base_dir.path_join("voicitool.cfg"))
		var what := I18n.t("Dieser Mod ist zu alt für den Server.")
		if str(mod.get("level", "")) != "block":
			what = I18n.t("Für den Server gibt es eine neuere Version des Mods.")
		var how := I18n.t("Starte das Spiel neu, dann holt er sich das Update.")
		if by_voicitool:
			how = I18n.t("Voicitool öffnen, dann wird er aktualisiert.")
		out.append("%s %s" % [what, how])
	var res = notes.get("restart")
	if res is Dictionary and Time.get_ticks_msec() - int(res.get("at", 0)) < 300000:
		out.append(I18n.t("Der Server bekommt gleich ein Update und startet kurz neu. Danach müsst ihr neu beitreten."))
	var srv = notes.get("server")
	if srv is Dictionary and str(srv.get("text", "")) != "":
		out.append(str(srv.text))
	return "\n".join(out)

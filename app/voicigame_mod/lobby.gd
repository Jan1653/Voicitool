extends CanvasLayer
## Voicigame-Menü über dem Spielmenü.
##   Auswahl   Lobby erstellen (dieser PC ist Host) oder Lobby beitreten (bei einem anderen Host)
##   Host      Raumcode, QR-Code, Link, Liste der Mitspieler, Start

signal start_requested(host_plays: bool)
signal join_requested
signal dub_requested(host_plays: bool)
signal closed

const UI = preload("ui.gd")
const I18n = preload("i18n.gd")
const Players = preload("players.gd")

var bridge: Node
var _bg: ColorRect
var _page: Control
var _code: Label
var _link: Label
var _qr: TextureRect
var _list: VBoxContainer
var _status: Label
var _host_plays: CheckBox
var _name_edit: LineEdit
var _qr_http: HTTPRequest
var update_note := ""            # setzt main.gd: Update installiert oder verfügbar
var _kick_armed := ""            # Spieler, bei dem „Entfernen“ schon einmal gedrückt wurde (zweiter Klick entfernt)


static func tr_(s: String, args: Array = []) -> String:
	return I18n.t(s, args)


func setup(b: Node) -> void:
	bridge = b
	layer = 90
	_bg = UI.backdrop(self)
	bridge.room_ready.connect(_on_room_ready)
	bridge.state_changed.connect(func(_s): _refresh())
	bridge.connection_changed.connect(func(_c): _refresh())
	bridge.error_received.connect(_on_error)
	bridge.room_lost.connect(_on_room_lost)
	bridge.queued.connect(_on_queued)
	if bridge.has_room():
		_show_host()     # Raum läuft schon (zurück aus dem Spiel): direkt die Host-Seite
	else:
		_show_choice()


func _clear_page() -> void:
	if is_instance_valid(_page):
		_page.queue_free()
	_page = Control.new()
	_page.set_anchors_preset(Control.PRESET_FULL_RECT)
	_bg.add_child(_page)


# ------------------------------------------------------------------
# Auswahl
# ------------------------------------------------------------------

func _show_choice() -> void:
	_clear_page()
	var box := UI.center_column(_page, 22)
	box.add_child(UI.label("VOICIGAME", 34, true, UI.ACCENT))
	box.add_child(UI.label(tr_("Mitspielen am Handy oder im Browser"), 24))
	box.add_child(UI.text(tr_("Erstelle eine Lobby, wenn das Spiel auf diesem PC läuft. Tritt bei, wenn jemand anderes Host ist."),
		22, 620, UI.MUTED))
	var gap := Control.new()
	gap.custom_minimum_size.y = 16
	box.add_child(gap)
	box.add_child(UI.button(tr_("Lobby erstellen"), _show_host, 420))
	box.add_child(UI.button(tr_("Lobby beitreten"), _on_join, 420))
	box.add_child(UI.button(tr_("Zurück"), _on_back, 420))
	box.add_child(_language_row())
	if update_note != "":
		box.add_child(UI.text(update_note, 18, 620, UI.ACCENT))


## Sprache des Mods: wie Windows oder fest gewählt. Wirkt sofort.
func _language_row() -> Control:
	var row := HBoxContainer.new()
	row.alignment = BoxContainer.ALIGNMENT_CENTER
	row.add_theme_constant_override("separation", 12)
	row.add_child(UI.label(tr_("Sprache"), 18, false, UI.MUTED))
	var pick := OptionButton.new()
	pick.add_theme_font_size_override("font_size", 18)
	pick.get_popup().add_theme_font_size_override("font_size", 18)
	pick.add_item(tr_("Automatisch (Windows)"))
	pick.set_item_metadata(0, "")
	var cur := I18n.chosen()
	for code in I18n.available():
		pick.add_item(str(I18n.LANG_NAMES.get(code, code)))
		pick.set_item_metadata(pick.item_count - 1, code)
		if code == cur:
			pick.select(pick.item_count - 1)
	pick.item_selected.connect(func(i):
		I18n.choose(str(pick.get_item_metadata(i)))
		_show_choice.call_deferred())
	row.add_child(pick)
	return row


func _on_join() -> void:
	join_requested.emit()
	queue_free()


# ------------------------------------------------------------------
# Host
# ------------------------------------------------------------------

func _show_host() -> void:
	_clear_page()
	# Das Spiel rechnet mit 1152 x 648
	var root := UI.margin(_page, 32)
	var cols := HBoxContainer.new()
	cols.add_theme_constant_override("separation", 32)
	root.add_child(cols)

	# Links: Raumcode, QR, Link
	var left := VBoxContainer.new()
	left.custom_minimum_size.x = 380
	left.add_theme_constant_override("separation", 8)
	cols.add_child(left)
	left.add_child(UI.label("VOICIGAME", 24, true, UI.ACCENT))
	left.add_child(UI.text(tr_("Mitspielen am Handy oder im Browser"), 18, 380))
	left.add_child(UI.label(tr_("Raumcode"), 16, false, UI.MUTED))
	_code = UI.label("", 72, true)   # Code kommt, sobald der Raum steht
	left.add_child(_code)
	_qr = TextureRect.new()
	_qr.custom_minimum_size = Vector2(220, 220)
	_qr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_qr.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT
	left.add_child(_qr)
	_link = UI.label("", 18, false, UI.ACCENT)
	left.add_child(_link)

	# Rechts: Spielerliste und Knöpfe
	var right := VBoxContainer.new()
	right.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	right.add_theme_constant_override("separation", 10)
	cols.add_child(right)
	right.add_child(UI.label(tr_("Spieler"), 26, true))
	_list = VBoxContainer.new()
	_list.add_theme_constant_override("separation", 8)
	_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	right.add_child(_list)
	_host_plays = CheckBox.new()
	_host_plays.text = tr_("Ich spiele am PC selbst mit")
	_host_plays.button_pressed = true
	_host_plays.add_theme_font_size_override("font_size", 18)
	right.add_child(_host_plays)
	# Eigener Name: so sehen die anderen den PC-Spieler (Punkte, „… ist dran“, Figuren im Dub-Modus)
	var name_row := HBoxContainer.new()
	name_row.add_theme_constant_override("separation", 12)
	name_row.add_child(UI.label(tr_("Dein Name"), 18, false, UI.MUTED))
	_name_edit = UI.line_edit(tr_("Dein Name"), 18, 260)
	_name_edit.max_length = 24
	var saved := Players.saved_name()
	_name_edit.text = saved if saved != "" else Players.profile_name(get_node_or_null("/root/Profile"))
	_name_edit.text_changed.connect(func(t): Players.save_name(t))
	name_row.add_child(_name_edit)
	right.add_child(name_row)
	_status = UI.label("", 17, false, UI.WARN)
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	right.add_child(_status)
	var buttons := HBoxContainer.new()
	buttons.add_theme_constant_override("separation", 12)
	buttons.add_child(UI.button(tr_("Weiter"), _on_start, 170, 52))
	buttons.add_child(UI.button(tr_("Synchronisieren"), _on_dub, 240, 52))
	buttons.add_child(UI.button(tr_("Zurück"), _on_back, 150, 52))
	right.add_child(buttons)

	if _qr_http == null:
		_qr_http = HTTPRequest.new()
		add_child(_qr_http)
		_qr_http.request_completed.connect(_on_qr_loaded)

	if bridge.has_room():
		_on_room_ready(bridge.room_code, bridge.join_url)
	else:
		_status.text = tr_("Raum wird erstellt …")
		bridge.create_room()


func _on_room_ready(code: String, join_url: String) -> void:
	if not is_instance_valid(_code):
		return
	_code.text = code
	_link.text = join_url.replace("https://", "").replace("http://", "")
	_qr_http.request(bridge.qr_url())
	_refresh()


func _on_qr_loaded(_result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if code != 200 or not is_instance_valid(_qr):
		return
	var img := Image.new()
	if img.load_png_from_buffer(body) == OK:
		_qr.texture = ImageTexture.create_from_image(img)


func _refresh() -> void:
	if not is_instance_valid(_list):
		return
	for c in _list.get_children():
		c.queue_free()
	var web: Array = bridge.web_players()
	if web.is_empty():
		_list.add_child(UI.text(tr_("Noch niemand da. Handy-Kamera auf den QR-Code halten oder den Link öffnen."), 18, 420, UI.MUTED))
	for p in web:
		var row := HBoxContainer.new()
		row.add_theme_constant_override("separation", 14)
		row.add_child(UI.label(("● " if p.get("connected", false) else "○ ") + str(p.get("name", "?")), 22))   # Knopf direkt dahinter
		var pid := str(p.get("id", ""))
		var armed := _kick_armed == pid
		row.add_child(UI.small_button(tr_("Wirklich?") if armed else tr_("Entfernen"), _on_kick.bind(pid), armed))
		_list.add_child(row)
	if not bridge.connected and bridge.has_room():
		_status.text = tr_("Verbindung zum Server wird aufgebaut …")
	elif bridge.has_room():
		_status.text = "" if web.size() else tr_("Warte auf Mitspieler.")


## Raum gibt es auf dem Server nicht mehr (Server neu gestartet, zu lange still): neuen anlegen.
func _on_room_lost() -> void:
	if not is_instance_valid(_code):
		return
	_code.text = ""
	_link.text = ""
	_qr.texture = null
	_status.text = tr_("Raum wird erstellt …")
	bridge.create_room()


## Spieler entfernen: erster Klick fragt nach, zweiter entfernt. Nach 4 s ohne zweiten Klick zurück.
func _on_kick(pid: String) -> void:
	if _kick_armed == pid:
		_kick_armed = ""
		bridge.kick(pid)
	else:
		_kick_armed = pid
		get_tree().create_timer(4.0).timeout.connect(_disarm_kick.bind(pid))
	_refresh()


func _disarm_kick(pid: String) -> void:
	if _kick_armed == pid:
		_kick_armed = ""
		_refresh()


func _on_queued(position: int) -> void:
	if is_instance_valid(_status):
		_status.text = tr_("Der Server ist gerade voll. Du bist in der Warteschlange auf Platz {}. Es geht automatisch weiter.", [position])


func _on_error(_code: String, message: String) -> void:
	if is_instance_valid(_status):
		_status.text = tr_(message)


func _on_start() -> void:
	_keep_name()
	var web: Array = bridge.web_players()
	if web.is_empty() and not _host_plays.button_pressed:
		_status.text = tr_("Es spielt noch niemand mit.")
		return
	start_requested.emit(_host_plays.button_pressed)


## Dub-Modus: Pack im Spiel wählen, Web-Spieler sprechen ihre Figuren im Browser.
func _on_dub() -> void:
	_keep_name()
	dub_requested.emit(_host_plays.button_pressed)


## Vorgeschlagener Name (aus dem Spielerprofil) gilt erst als eigener, wenn es losgeht.
func _keep_name() -> void:
	if is_instance_valid(_name_edit) and _name_edit.text.strip_edges() != "":
		Players.save_name(_name_edit.text)


func _on_back() -> void:
	if bridge.has_room():
		bridge.close_room()
	closed.emit()
	queue_free()

extends RefCounted
## Web-Spieler als Gruppenmitglieder des Spiels.
##
## Das Spiel baut jeden Mitspieler aus einem Spieler-Pack (Name, Farben, Bild, Sprüche) und
## merkt sich zu jedem ein Mikrofon. Für Web-Spieler legen wir ein eigenes Pack an und tragen
## statt eines Mikrofons ein Kennzeichen ein, an dem der Mod erkennt, wer gerade dran ist.

const PACK_ROOT := "user://game/packs_player/"
const PACK_PREFIX := "Voicigame "
const DEVICE_PREFIX := "voicigame:"
const I18n = preload("i18n.gd")
const COLORS := ["#29b6f6", "#ef5350", "#66bb6a", "#ffca28", "#ab47bc", "#ff7043", "#26c6da", "#ec407a"]


const CONFIG := "user://voicigame.cfg"


## Eigener Name, den der Spieler im Mod eingetragen hat (Host und Beitreten teilen ihn). Leer = keiner.
static func saved_name() -> String:
	var cfg := ConfigFile.new()
	if cfg.load(CONFIG) != OK:
		return ""
	return str(cfg.get_value("join", "name", "")).strip_edges().left(24)


static func save_name(name: String) -> void:
	var cfg := ConfigFile.new()
	if cfg.load(CONFIG) != OK and FileAccess.file_exists(CONFIG):
		return   # Datei da, aber nicht lesbar: nichts überschreiben
	cfg.set_value("join", "name", name.strip_edges().left(24))
	cfg.save(CONFIG)


## Name der Figur aus dem Spielerprofil des Spiels (Vorschlag, wenn noch kein Name eingetragen ist).
static func profile_name(profile: Node) -> String:
	var pack := str(profile.get("contestant")) if profile else ""
	if pack == "":
		return ""
	var path := PACK_ROOT + pack + "/config_player.json"
	var data = JSON.parse_string(FileAccess.get_file_as_string(path)) if FileAccess.file_exists(path) else null
	return str(data.get("name", pack)) if data is Dictionary else pack


static func device_for(player_id: String) -> String:
	return DEVICE_PREFIX + player_id


static func player_for_device(device: String) -> String:
	return device.trim_prefix(DEVICE_PREFIX) if device.begins_with(DEVICE_PREFIX) else ""


## Pack für einen Web-Spieler anlegen. -> Ordnername (so wie das Spiel ihn erwartet)
static func make_pack(index: int, player_id: String, name: String) -> String:
	var folder := "%s%d %s" % [PACK_PREFIX, index + 1, _safe(name)]
	var dir := PACK_ROOT + folder + "/"
	DirAccess.make_dir_recursive_absolute(dir)
	var color: String = COLORS[index % COLORS.size()]
	var cfg := {"name": name, "color1": color, "color2": "#f5f5f5", "introduction": I18n.t("Aus dem Browser zugeschaltet:")}   # das Spiel setzt Name und „!“ dahinter
	var f := FileAccess.open(dir + "config_player.json", FileAccess.WRITE)
	if f:
		f.store_string(JSON.stringify(cfg, "\t"))
		f.close()
	_avatar(Color(color)).save_png(dir + "player.png")
	for talk in ["talk_greet", "talk_cheer", "talk_upset"]:
		_silence().save_to_wav(dir + talk + ".wav")
	var marker := FileAccess.open(dir + ".voicigame", FileAccess.WRITE)
	if marker:
		marker.store_string(player_id)
		marker.close()
	return folder


## Alle Packs, die voicigame angelegt hat, wieder wegräumen (in den Papierkorb).
static func remove_all() -> void:
	var dir := DirAccess.open(PACK_ROOT)
	if dir == null:
		return
	for sub in dir.get_directories():
		if sub.begins_with(PACK_PREFIX) and FileAccess.file_exists(PACK_ROOT + sub + "/.voicigame"):
			OS.move_to_trash(ProjectSettings.globalize_path(PACK_ROOT + sub))


static func _safe(name: String) -> String:
	var out := ""
	for ch in name:
		out += ch if ch.is_valid_identifier() or ch in " -" or ch.unicode_at(0) > 127 else "_"
	return out.strip_edges().left(24) if out.strip_edges() != "" else "Spieler"


static func _avatar(color: Color) -> Image:
	var size := 256
	var img := Image.create(size, size, false, Image.FORMAT_RGBA8)
	img.fill(color.darkened(0.35))
	var c := Vector2(size / 2.0, size / 2.0)
	for y in size:
		for x in size:
			var d := Vector2(x, y).distance_to(c)
			if d < size * 0.42:
				img.set_pixel(x, y, color.lightened(0.15 * (1.0 - d / (size * 0.42))))
	return img


static func _silence() -> AudioStreamWAV:
	var s := AudioStreamWAV.new()
	s.format = AudioStreamWAV.FORMAT_16_BITS
	s.mix_rate = 22050
	var data := PackedByteArray()
	data.resize(2205 * 2)   # 0,1 s Stille
	s.data = data
	return s

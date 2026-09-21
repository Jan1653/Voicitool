extends RefCounted
## Bausteine für die Mod-Oberflächen, aus Knöpfen und Schriften des Spiels,
## damit Lobby und Beitreten-Bildschirm wie ein Teil davon aussehen.

const BUTTON_SCENE := "res://scene/module/button/button_cv.tscn"
const FONT_BOLD := "res://graphic/font/Waukegan LDO Extended Bold.ttf"
const FONT_TEXT := "res://graphic/font/DuruSans-Regular.ttf"
const ACCENT := Color(0.55, 0.85, 1.0)
const MUTED := Color(0.7, 0.75, 0.8)
const WARN := Color(1.0, 0.85, 0.45)


## Kleiner schlichter Knopf (z. B. „Entfernen“ in Spielerlisten). warn: rote Schrift.
static func small_button(text: String, cb: Callable, warn := false) -> Button:
	var b := Button.new()
	b.text = text
	b.focus_mode = Control.FOCUS_NONE
	b.add_theme_font_size_override("font_size", 16)
	var f := font(FONT_TEXT)
	if f:
		b.add_theme_font_override("font", f)
	if warn:
		for state in ["font_color", "font_hover_color", "font_pressed_color"]:
			b.add_theme_color_override(state, Color(1.0, 0.45, 0.45))
	b.pressed.connect(cb)
	return b


static func font(path: String) -> Font:
	return load(path) if ResourceLoader.exists(path) else null


static func label(text: String, size: int, bold := false, color := Color.WHITE) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", color)
	var f := font(FONT_BOLD if bold else FONT_TEXT)
	if f:
		l.add_theme_font_override("font", f)
	return l


## Mehrzeiliger Text, der in der gegebenen Breite umbricht.
static func text(content: String, size: int, width: float, color := Color.WHITE) -> Label:
	var l := label(content, size, false, color)
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	l.custom_minimum_size.x = width
	return l


## Knopf im Stil des Spiels (ButtonCV), sonst ein normaler Knopf.
static func button(content: String, cb: Callable, width := 280.0, height := 64.0) -> Control:
	if ResourceLoader.exists(BUTTON_SCENE):
		var b = load(BUTTON_SCENE).instantiate()
		b.custom_minimum_size = Vector2(width, height)
		b.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
		b.button_clicked.connect(cb)
		b.ready.connect(func(): if b.has_method("set_first_label_text"): b.set_first_label_text(content), CONNECT_ONE_SHOT)
		var l := Label.new()   # ButtonCV zeigt den Text über ein Label-Kind
		l.name = "Caption"
		l.text = content
		l.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		l.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		l.set_anchors_preset(Control.PRESET_FULL_RECT)
		l.add_theme_font_size_override("font_size", 26 if height >= 60 else 22)
		var f := font(FONT_BOLD)
		if f:
			l.add_theme_font_override("font", f)
		b.add_child(l)
		return b
	var fallback := Button.new()
	fallback.text = content
	fallback.custom_minimum_size = Vector2(width, height)
	fallback.pressed.connect(cb)
	return fallback


## Beschriftung eines Knopfs aus button() nachträglich ändern.
static func set_button_text(b: Control, content: String) -> void:
	if b is Button:
		b.text = content
		return
	var l := b.get_node_or_null("Caption")
	if l:
		l.text = content
	if b.has_method("set_first_label_text"):
		b.set_first_label_text(content)


## Dunkler, bildschirmfüllender Hintergrund, der Klicks aufs Menü dahinter abfängt.
static func backdrop(parent: Node) -> ColorRect:
	var bg := ColorRect.new()
	bg.color = Color(0.03, 0.06, 0.11, 0.995)
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	bg.mouse_filter = Control.MOUSE_FILTER_STOP
	parent.add_child(bg)
	return bg


## Rand um den Inhalt.
static func margin(parent: Node, px := 56) -> MarginContainer:
	var root := MarginContainer.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	for side in ["left", "right", "top", "bottom"]:
		root.add_theme_constant_override("margin_" + side, px)
	parent.add_child(root)
	return root


static func line_edit(placeholder: String, size: int, width: float) -> LineEdit:
	var e := LineEdit.new()
	e.placeholder_text = placeholder
	e.custom_minimum_size = Vector2(width, size * 1.9)
	e.add_theme_font_size_override("font_size", size)
	var f := font(FONT_BOLD)
	if f:
		e.add_theme_font_override("font", f)
	return e


## Zentrierte Spalte (für Auswahl- und Eingabeseiten).
static func center_column(parent: Node, gap := 18) -> VBoxContainer:
	var c := CenterContainer.new()
	c.set_anchors_preset(Control.PRESET_FULL_RECT)
	parent.add_child(c)
	var box := VBoxContainer.new()
	box.alignment = BoxContainer.ALIGNMENT_CENTER
	box.add_theme_constant_override("separation", gap)
	c.add_child(box)
	return box

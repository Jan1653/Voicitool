extends RefCounted
## Liest WAV-Dateien (Handy-Aufnahmen, Clips) ohne Umweg über den Importer.
## PCM mit 8, 16, 24 oder 32 Bit und 32-Bit-Gleitkomma; alles wird zu 16 Bit.
## Funktioniert in allen Godot-4-Versionen.


static func from_wav_bytes(bytes: PackedByteArray) -> AudioStreamWAV:
	if bytes.size() < 44 or bytes.slice(0, 4).get_string_from_ascii() != "RIFF":
		return null
	var pos := 12
	var channels := 1
	var rate := 44100
	var bits := 16
	var is_float := false
	var fmt_ok := false
	var data := PackedByteArray()
	while pos + 8 <= bytes.size():
		var id := bytes.slice(pos, pos + 4).get_string_from_ascii()
		var size := bytes.decode_u32(pos + 4)
		var body := pos + 8
		if id == "fmt ":
			var audio_format := bytes.decode_u16(body)
			channels = bytes.decode_u16(body + 2)
			rate = bytes.decode_u32(body + 4)
			bits = bytes.decode_u16(body + 14)
			if audio_format == 0xFFFE and size >= 26:   # WAVE_FORMAT_EXTENSIBLE: echtes Format im Untertyp
				audio_format = bytes.decode_u16(body + 24)
			is_float = audio_format == 3
			fmt_ok = (audio_format == 1 and bits in [8, 16, 24, 32]) or (is_float and bits == 32)
		elif id == "data":
			data = bytes.slice(body, mini(body + size, bytes.size()))
		pos = body + size + (size & 1)
	if not fmt_ok or data.is_empty() or channels < 1:
		return null
	if bits != 16 or is_float:
		data = _to_16_bit(data, bits, is_float)
	if channels > 2:
		data = _first_two_channels(data, channels)
		channels = 2
	var s := AudioStreamWAV.new()
	s.format = AudioStreamWAV.FORMAT_16_BITS
	s.mix_rate = rate
	s.stereo = channels == 2
	s.data = data
	return s


static func _to_16_bit(data: PackedByteArray, bits: int, is_float: bool) -> PackedByteArray:
	var step := bits / 8
	var n := data.size() / step
	var out := PackedByteArray()
	out.resize(n * 2)
	for i in n:
		var v := 0
		var o := i * step
		if is_float:
			v = int(clampf(data.decode_float(o), -1.0, 1.0) * 32767.0)
		elif bits == 8:
			v = (data[o] - 128) << 8
		elif bits == 24:
			v = data.decode_s16(o + 1)
		else:
			v = data.decode_s32(o) >> 16
		out.encode_s16(i * 2, v)
	return out


static func _first_two_channels(data: PackedByteArray, channels: int) -> PackedByteArray:
	var frames := data.size() / (2 * channels)
	var out := PackedByteArray()
	out.resize(frames * 4)
	for f in frames:
		out.encode_s16(f * 4, data.decode_s16(f * channels * 2))
		out.encode_s16(f * 4 + 2, data.decode_s16(f * channels * 2 + 2))
	return out


## Samples als Floats (-1..1), falls die Bewertung des Spiels Rohdaten braucht.
static func to_samples(stream: AudioStreamWAV) -> PackedFloat32Array:
	var out := PackedFloat32Array()
	var d := stream.data
	var step := 4 if stream.stereo else 2
	out.resize(d.size() / step)
	for i in out.size():
		var v := d.decode_s16(i * step)
		if stream.stereo:
			v = int((v + d.decode_s16(i * step + 2)) / 2)
		out[i] = v / 32768.0
	return out


## AudioStreamWAV (16 Bit) als WAV-Datei. pad_seconds: Stille vorneweg.
static func to_wav_bytes(stream: AudioStreamWAV, pad_seconds := 0.0) -> PackedByteArray:
	var channels := 2 if stream.stereo else 1
	var rate := stream.mix_rate
	var pad := PackedByteArray()
	pad.resize(int(rate * pad_seconds) * channels * 2)
	var data := pad + stream.data
	var out := PackedByteArray()
	out.resize(44)
	out.encode_u32(0, 0x46464952)          # "RIFF"
	out.encode_u32(4, 36 + data.size())
	out.encode_u32(8, 0x45564157)          # "WAVE"
	out.encode_u32(12, 0x20746d66)         # "fmt "
	out.encode_u32(16, 16)
	out.encode_u16(20, 1)                  # PCM
	out.encode_u16(22, channels)
	out.encode_u32(24, rate)
	out.encode_u32(28, rate * channels * 2)
	out.encode_u16(32, channels * 2)
	out.encode_u16(34, 16)
	out.encode_u32(36, 0x61746164)         # "data"
	out.encode_u32(40, data.size())
	out.append_array(data)
	return out

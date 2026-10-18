// px_process — выборка цвета из входного TOP и обработка ячейки (pixel-engine, glslPOP).
//
// Вход 0 — точки geo_cells: Tex (UV на холсте, 0–1), bright, col (float3), mute (uint), active (uint).
// Сэмплер sIn — подготовленный вход (filter linear → билинейная выборка; extend zero → UV за холстом даёт чёрный).
// Униформ uMaster — пара Master компонента. Выход — Color (float4; альфа = 0 — это «пустой» компонент для W/A
// в профилях без значения белого: блок dmxfixturePOP берёт его свизлом Color(...,3)).
// API glslPOP: чтение TDIn_<Attr>(вход, индекс), запись <Attr>[индекс] (не glsladvancedPOP).
//
// Гамма, баланс белого, RGB→RGBW, лимит мощности — спека color-power (здесь не считаются).

void main()
{
	const uint id = TDIndex();
	if (id >= TDNumElements())
		return;
	vec2 uv = TDIn_Tex(0, id).xy;
	vec4 s = texture(sIn, uv);
	float k = uMaster * TDIn_bright(0, id)
	        * (1.0 - float(TDIn_mute(0, id)))
	        * float(TDIn_active(0, id));
	vec3 tint = TDIn_col(0, id);
	Color[id] = vec4(s.rgb * tint * k, 0.0);
}

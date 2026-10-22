// locate — подмена цвета ячеек по цели и режиму Locate (locate-test, glslPOP в hook между in1 и out1).
//
// Эталон — dsm_locate.expected_color (формулы в design, раздел hook/locate). Меняется только Color; число и порядок
// точек, остальные атрибуты и служебные каналы (мимо hook) не трогаются. Шейдер стоит в обходе (bypass), когда Locate
// и тест-паттерн выключены (9.5).
//
// Входы: точки hook/in1 — fx (uint, индекс фикстуры), cell (uint, data_index), active (uint 0/1), Color (float4).
// Массивы (texturebuffer, сэмпл = фикстура): uFx = (target, number, ncells, offset), uAddr = (uni, ch).
// Униформы: uMode (0 blink, 1 direction, 2 number, 3 address, 4 fill, 5 rgbw, 6 chase), uOthers (0 solo, 1 dim, 2 over),
// uLevel, uDim, uBlinkHz, uSmooth (0/1), uRunSec, uTestColor (vec3), uTime (absTime.seconds), uFreeze (−1 — живое время,
// ≥ 0 — фиксированное для стенда), uTotal (сумма ячеек выводимых), uNfx (число выводимых).
// Все фазы — через frac/floor (устойчиво во float32 при большом времени).

float fr(float x)
{
	return x - floor(x);
}

vec3 targetColor(int mode, float t)
{
	if (mode == 0) {                                   // мигание: меандр или плавное
		float k = (uSmooth > 0.5) ? 0.5 - 0.5 * cos(6.283185307179586 * fr(t * uBlinkHz))
		                          : (fr(t * uBlinkHz) < 0.5 ? 1.0 : 0.0);
		return vec3(1.0) * (uLevel * k);
	}
	return vec3(0.0);                                  // режимы 1–3 — задача 3.2
}

void main()
{
	const uint id = TDIndex();
	if (id >= TDNumElements())
		return;
	vec4 c = TDIn_Color(0, id);
	if (TDIn_active(0, id) == 0u) {                    // отключённая ячейка — без изменений (2.6)
		Color[id] = c;
		return;
	}
	int fx = int(TDIn_fx(0, id));
	vec4 f = texelFetch(uFx, fx);                      // target, number, ncells, offset
	float t = (uFreeze >= 0.0) ? uFreeze : uTime;
	int mode = int(uMode + 0.5);
	vec3 rgb;
	if (mode >= 4) {
		rgb = c.rgb;                                   // тест-паттерны — задача 3.2
	} else if (f.x > 0.5) {
		rgb = targetColor(mode, t);                    // цель: цвет режима × уровень, без контента и Master (9.2)
	} else {
		int others = int(uOthers + 0.5);               // остальные: соло / приглушить / поверх (2.2–2.4)
		rgb = (others == 0) ? vec3(0.0) : ((others == 1) ? c.rgb * uDim : c.rgb);
	}
	Color[id] = vec4(rgb, c.a);
}

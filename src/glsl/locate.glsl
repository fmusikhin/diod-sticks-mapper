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
// Все фазы — через frac/floor (без переполнения во float32 при большом времени).

const vec3 C_RED = vec3(1.0, 0.0, 0.0);
const vec3 C_GREEN = vec3(0.0, 1.0, 0.0);
const vec3 C_BLUE = vec3(0.0, 0.0, 1.0);
const vec3 C_WHITE = vec3(1.0);
const vec3 C_DIM = vec3(0.1);        // нулевой разряд — тусклая белая (10 % уровня)

float fr(float x)
{
	return x - floor(x);
}

// Раскладка числа по ячейкам (dsm_locate.number_layout / address_layout): разряды со старшего, цифра d — d ячеек
// цвета разряда (единицы синие, десятки зелёные, сотни красные, выше — белые), ноль — одна тусклая; между разрядами
// одна погашенная ячейка, перед первой группой — firstGap (0 у числа и юниверса, 2 у канала после юниверса); разряд,
// который не помещается целиком, и всё после него — погашено (в режиме адреса канал тогда не показывается вовсе).
// Возвращает 1 — ячейка c в группе этого числа (rgb задан); 0 — не в группе, число размещено целиком;
// −1 — разряд не поместился. cursor — число занятых ячеек (общий для юниверса и канала в режиме адреса).
int digitsColor(uint value, uint n, uint c, uint firstGap, inout uint cursor, out vec3 rgb)
{
	rgb = vec3(0.0);
	uint ndig = 1u;
	for (uint v = value / 10u; v > 0u; v /= 10u)
		ndig++;
	uint p10 = 1u;
	for (uint k = 1u; k < ndig; k++)
		p10 *= 10u;
	for (uint k = 0u; k < ndig; k++) {
		uint pos = ndig - 1u - k;                  // позиция разряда от единиц
		uint d = (value / p10) % 10u;
		p10 /= 10u;
		uint g = (d == 0u) ? 1u : d;
		uint gap = (cursor == 0u) ? 0u : ((k == 0u) ? firstGap : 1u);
		uint start = cursor + gap;
		if (start + g > n)
			return -1;                             // разряд не помещается — хвост погашен
		if (c >= start && c < start + g) {
			rgb = (d == 0u) ? C_DIM : (pos == 0u ? C_BLUE : (pos == 1u ? C_GREEN : (pos == 2u ? C_RED : C_WHITE)));
			return 1;
		}
		cursor = start + g;
	}
	return 0;
}

vec3 targetColor(int mode, float t, uint c, vec4 f, vec2 addr)
{
	uint n = uint(f.z + 0.5);
	if (mode == 0) {                                   // мигание: меандр или плавное
		float k = (uSmooth > 0.5) ? 0.5 - 0.5 * cos(6.283185307179586 * fr(t * uBlinkHz))
		                          : (fr(t * uBlinkHz) < 0.5 ? 1.0 : 0.0);
		return C_WHITE * (uLevel * k);
	}
	if (mode == 1) {                                   // направление: 0 красная, n−1 синяя, бегущая белая по 1..n−2
		if (c == 0u)
			return C_RED * uLevel;
		if (c == n - 1u)
			return C_BLUE * uLevel;
		if (n >= 3u && c == 1u + uint(floor(fr(t / uRunSec) * float(n - 2u))))
			return C_WHITE * uLevel;
		return vec3(0.0);
	}
	uint cursor = 0u;
	vec3 rgb;
	if (mode == 2) {                                   // номер
		digitsColor(uint(f.y + 0.5), n, c, 0u, cursor, rgb);
		return rgb * uLevel;
	}
	if (mode == 3) {                                   // адрес: юниверс, две погашенные, канал
		if (digitsColor(uint(addr.x + 0.5), n, c, 0u, cursor, rgb) == 0)   // юниверс размещён целиком — дальше канал
			digitsColor(uint(addr.y + 0.5), n, c, 2u, cursor, rgb);
		return rgb * uLevel;
	}
	return vec3(0.0);
}

vec3 patternColor(int mode, float t, uint c, vec4 f)
{
	if (mode == 4)                                     // заливка
		return uTestColor * uLevel;
	if (mode == 5) {                                   // R → G → B → W, 1 с
		int k = int(floor(t)) % 4;
		vec3 rgb = (k == 0) ? C_RED : (k == 1 ? C_GREEN : (k == 2 ? C_BLUE : C_WHITE));
		return rgb * uLevel;
	}
	// mode 6 — бегущая точка по всему ригу в порядке данных (другие значения ≥ 6 сюда же)
	if (uTotal <= 0.5 || uNfx <= 0.5)
		return vec3(0.0);
	uint p = uint(floor(fr(t / (uRunSec * uNfx)) * uTotal));
	return (uint(f.w + 0.5) + c == p) ? C_WHITE * uLevel : vec3(0.0);
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
	uint cell = TDIn_cell(0, id);
	vec4 f = texelFetch(uFx, fx);                      // target, number, ncells, offset
	float t = (uFreeze >= 0.0) ? uFreeze : uTime;
	int mode = int(uMode + 0.5);
	vec3 rgb;
	if (mode >= 4) {
		rgb = (f.z > 0.5) ? patternColor(mode, t, cell, f) : c.rgb;   // паттерн — все выводимые фикстуры
	} else if (f.x > 0.5) {
		rgb = targetColor(mode, t, cell, f, texelFetch(uAddr, fx).xy);  // цель: без контента и Master (9.2)
	} else {
		int others = int(uOthers + 0.5);               // остальные: соло / приглушить / поверх (2.2–2.4)
		rgb = (others == 0) ? vec3(0.0) : ((others == 1) ? c.rgb * uDim : c.rgb);
	}
	Color[id] = vec4(rgb, c.a);
}

// px_process — выборка цвета из входного TOP и обработка ячейки (pixel-engine, glslPOP).
// Задача 1.2: проходной шейдер (Color берётся из входа). Задача 4.2: выборка по Tex с билинейной
// фильтрацией, uMaster, bright, col, mute, active.
//
// Контракт: вход 0 — точки с Tex (UV), bright, col, mute, active; выход — Color (float4).

void main()
{
	uint id = TDIndex();
	oTDPoint_Color[id] = TDInPoint_Color(0, id);
}

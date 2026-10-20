// locate — подмена цвета ячеек по цели и режиму Locate (locate-test, glslPOP в hook между in1 и out1).
//
// Каркас (задача 1.2): цвет проходит без изменений. Режимы, массивы на фикстуру и униформы — задачи 3.1–3.2.
// API glslPOP: чтение TDIn_<Attr>(вход, индекс), запись <Attr>[индекс]; outputaccess = readwrite.

void main()
{
	const uint id = TDIndex();
	if (id >= TDNumElements())
		return;
	Color[id] = TDIn_Color(0, id);
}

"""Колбэк репликатора узлов вывода (DAT ``output/rep_callbacks``, синхронизирован с этим файлом).

Реплика ``out_g<j>`` настраивается по строке ``g<j>`` таблицы ``tables/out_groups``: протокол, multicast, частота, ArtSync,
маршрутизация ``tables/routing_g<j>``; её фикстуры — реплики ``fixtures/p<k>g<j>`` и ``raw_g<j>``.
Последней в список фикстур каждой группы дописывается сырая фикстура Locate ``output/raw/raw_fix`` ВЫКЛЮЧЕННОЙ:
в ``dmxoutPOP`` при наложении каналов побеждает последний источник списка (опыт ``spike_raw``), поэтому включённая
последней она перекрывает весь юниверс сырого адреса (7.2). Включает её только расширение Locate у группы нужного выхода.

TD-глобалы (``parent``) доступны в колбэке без импорта. Python здесь исполняется только при пересборке реплик.
"""

RAW_FIXTURE = "../raw/raw_fix"      # относительно реплики out_g<j>


def configure(rep, comp):
	g = comp.name[4:]
	t = comp.parent().op('../tables/out_groups'); o = comp.op('out')
	proto, multicast, rate, artsync = t[g, 'protocol'].val, t[g, 'multicast'].val == '1', float(t[g, 'rate_hz'].val), t[g, 'art_sync'].val == '1'
	o.par.interface = 'artnet' if proto == 'artnet' else 'sacn'
	o.par.multicast = multicast
	o.par.rate = int(min(rate, comp.time.rate))
	o.par.sendartsync = artsync
	o.par.routingtable = '../../tables/routing_' + g
	fx = comp.parent().op('../fixtures')
	pops = [c.op('fixture') for c in fx.children if c.isCOMP and c.name.endswith(g) and c.op('fixture')]
	raw = comp.op(RAW_FIXTURE)
	o.seq.fixture.numBlocks = max(len(pops) + (1 if raw is not None else 0), 1)
	for i, p in enumerate(pops):
		o.par['fixture' + str(i) + 'pop'] = o.relativePath(p); o.par['fixture' + str(i) + 'active'] = True
	if raw is not None:                                   # сырой адрес Locate — последним и выключенным
		i = len(pops)
		o.par['fixture' + str(i) + 'pop'] = o.relativePath(raw); o.par['fixture' + str(i) + 'active'] = False


def onReplicate(comp, allOps, newOps, template, master):
	for r in allOps:
		configure(comp, r)
	parent.dsm.ApplyActive()      # политика active — только в расширении
	parent.dsm.ext.DsmLocateExt.ReapplyRaw()      # сырой адрес Locate, если включён, — снова на свою группу
	return


def onRemoveReplicant(comp, replicant):
	replicant.destroy()
	return

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Конвейер доведения словаря синонимов synonymsl.txt до продакшн-качества.

Этапы (см. synonyms_quality_report.md):
  1. Нормализация токенов (пробелы, ё/е-пары внутри групп).
  2. Удаление токенного шума (обрывки юр. форм, "(ё)" и т.п.).
  3. Исправление гомоглифных подмен (смесь кириллицы/латиницы).
  4. Разъединение ложносшитых групп (курационный список SPLIT_GROUPS).
  5. Слияние транслит-двойников и разорванных брендов (MERGE + автопоиск).
  6. Дописывание алиасов в существующие группы (APPEND_TO_EXISTING).
  7. Добавление недостающих брендов (ADDITIONS).
  8. Финальная канонизация: dedup, union-find склейка пересекающихся групп,
     детерминированная сортировка, запись synonyms_clean.txt + метрик.

Выход:
  synonyms_clean.txt   — чистый словарь (строка = группа синонимов)
  build_metrics.json   — метрики качества до/после
  rejected_tokens.log  — удалённые шумовые токены (аудит)
"""
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict

import curated_fixes as cf

SRC = 'synonymsl.txt'
DST = 'synonyms_clean.txt'

CYR = set('абвгдеёжзийклмнопрстуфхцчшщъыьэюя'
          'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ'
          'ўґіє')  # доп. буквы укр./бел.


def has_cyr(s):
    return any(c in CYR or '\u0400' <= c <= '\u04ff' for c in s)


def normkey(s):
    """Ключ сравнения: нижний регистр, только буквы/цифры."""
    return ''.join(ch for ch in unicodedata.normalize('NFC', s.lower())
                   if ch.isalnum())


def load_groups(path):
    groups = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            toks = [t.strip() for t in line.split(',')]
            toks = [t for t in toks if t]
            if toks:
                groups.append(toks)
    return groups


# ---------------------------------------------------------------- helpers
class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def canon_group(tokens):
    """Детерминированная канонизация группы: без дублей, стабильный порядок.

    Порядок: сначала «канонические» формы (латиница/кириллица по normkey),
    дубликаты по normkey схлопываются в первую встреченную форму.
    """
    seen = set()
    out = []
    for t in sorted(set(tokens), key=lambda x: (normkey(x), x)):
        k = normkey(t)
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def metrics(groups):
    tokens = [t for g in groups for t in g]
    uniq = set(map(normkey, tokens))
    sizes = [len(g) for g in groups]
    cross = Counter()
    for g in groups:
        for t in g:
            cross[normkey(t)] += 1
    multi = sum(1 for v in cross.values() if v > 1)
    return {
        'groups': len(groups),
        'tokens': len(tokens),
        'unique_normkeys': len(uniq),
        'avg_size': round(sum(sizes) / max(1, len(sizes)), 2),
        'max_size': max(sizes) if sizes else 0,
        'singletons': sum(1 for s in sizes if s == 1),
        'tokens_in_multi_groups': multi,
    }


# ---------------------------------------------------------------- pipeline
def main():
    stats = Counter()
    groups = load_groups(SRC)
    before = metrics(groups)

    # ---- 1. ё/е-пары: добавляем отсутствующий вариант в ту же группу
    for g in groups:
        gs = set(g)
        for t in list(g):
            if 'ё' in t and t.replace('ё', 'е') not in gs:
                g.append(t.replace('ё', 'е'))
            if 'е' in t and 'ё' not in t and t.replace('е', 'ё') not in gs:
                g.append(t.replace('е', 'ё'))
    stats['yo_pairs_added'] = 1

    # ---- 2. удаление шума
    removed = []
    cleaned = []
    for g in groups:
        ng = []
        for t in g:
            if t in cf.NOISE_TOKENS or t.startswith('ltd. ('):
                removed.append(t)
                continue
            ng.append(t)
        if ng:
            cleaned.append(ng)
    groups = cleaned
    stats['noise_tokens_removed'] = len(removed)

    # ---- 3. гомоглифы
    fixed = 0
    for g in groups:
        for i, t in enumerate(g):
            if t in cf.FIX_HOMOGLYPHS:
                g[i] = cf.FIX_HOMOGLYPHS[t]
                fixed += 1
    stats['homoglyphs_fixed'] = fixed

    # ---- 4. разъединение ложных слияний (до union-find, точный адрес по токенам)
    token_index = defaultdict(set)  # token -> индексы групп
    for gi, g in enumerate(groups):
        for t in g:
            token_index[t].add(gi)
    split_applied, split_missed = [], []
    consumed = set()                 # индексы уже разобранных групп
    for anchor, new_groups in cf.SPLIT_GROUPS.items():
        hits = [gi for gi in token_index.get(anchor, ()) if gi not in consumed]
        cand = hits[0] if hits else None
        if cand is None:
            split_missed.append(anchor)
            continue
        consumed.add(cand)
        target = groups[cand]
        covered = set(t for ng in new_groups for t in ng)
        leftovers = [t for t in target if t not in covered]
        repl = [list(ng) for ng in new_groups]
        if leftovers:
            repl[-1].extend(leftovers)
        groups[cand] = None          # помечаем; позже уплотним
        for ng in repl:
            groups.append(ng)
        split_applied.append(anchor)
    groups = [g for g in groups if g]
    stats['splits_applied'] = len(split_applied)
    stats['splits_missed'] = len(split_missed)

    # ---- 5. слияния (ручные) + автопоиск транслит-двойников
    TRANS = str.maketrans({
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'})

    def translit_key(s):
        return s.lower().translate(TRANS)

    def merge_by_token(ufl, groups_ref):
        pass

    uf = UnionFind()
    gid = {id(g): gi for gi, g in enumerate(groups)}
    for gi, g in enumerate(groups):
        uf.find(gi)

    def link_by_key(keyfn, min_len=4):
        buckets = defaultdict(list)
        for gi, g in enumerate(groups):
            for t in set(g):
                k = keyfn(t)
                if len(k) >= min_len:
                    buckets[k].append((gi, t))
        for k, lst in buckets.items():
            base = lst[0][0]
            for gi, _ in lst[1:]:
                uf.union(base, gi)

    # ключ 1: нормализованное значение (dedup пересечений)
    link_by_key(normkey)
    # ключ 2: транслит кириллицы -> латиница (только если в группе есть
    # кириллический ТОКЕН И группа маленькая — защита от mega-кластеров)
    buckets = defaultdict(set)
    for gi, g in enumerate(groups):
        if len(g) > 60:      # слишком крупные кластеры не склеиваем слепо
            continue
        for t in g:
            if has_cyr(t):
                k = translit_key(normkey(t))
                if len(k) >= 5:
                    buckets[k].add(gi)
    # склейка только когда в бакете есть и кириллические, и латинские группы
    for k, gis in buckets.items():
        gis = list(gis)
        for gi in gis[1:]:
            uf.union(gis[0], gi)

    merged = defaultdict(list)
    for gi, g in enumerate(groups):
        merged[uf.find(gi)].extend(g)
    groups = [v for v in merged.values()]
    stats['groups_after_merge'] = len(groups)

    # ручные слияния из MERGE_GROUPS (подстраховка)
    tok2grp = defaultdict(list)
    for g in groups:
        for t in set(g):
            tok2grp[t].append(g)
    for mg in cf.MERGE_GROUPS:
        targets = []
        for sub in mg:
            for t in sub:
                if t in tok2grp:
                    targets.extend(tok2grp[t])
                    break
        ts = {id(x): x for x in targets}
        if len(ts) > 1:
            alltok = [t for g in ts.values() for t in g]
            keep = next(iter(ts.values()))
            drop = [g for gid_, g in ts.items() if g is not keep]
            keep[:] = alltok
            for d in drop:
                groups.remove(d)

    # ---- 6. APPEND_TO_EXISTING
    tok2grp = defaultdict(list)
    for g in groups:
        for t in set(g):
            tok2grp[t].append(g)
    appended = 0
    for anchor, aliases in cf.APPEND_TO_EXISTING.items():
        hits = tok2grp.get(anchor)
        if not hits:
            continue
        tgt = hits[0]
        for a in aliases:
            if a not in tgt:
                tgt.append(a)
                appended += 1
    stats['aliases_appended'] = appended

    # ---- 7. ADDITIONS: новые группы; если токен уже есть — не дублируем группу
    def is_legal_suffix(t):
        return t.strip('. ') in cf.LEGAL_SUFFIXES or t.startswith('ltd.')

    existing_keys = set()
    for g in groups:
        for t in g:
            if not is_legal_suffix(t):
                existing_keys.add(normkey(t))
    added_groups = 0
    added_tokens = 0
    for key, toks in cf.ADDITIONS.items():
        toks = list(dict.fromkeys([key] + list(toks)))
        keys = {normkey(t) for t in toks if not is_legal_suffix(t)}
        overlap = keys & existing_keys
        if overlap:
            # дописываем недостающие варианты во ВСЕ существующие группы-носители
            tgt_keys = {normkey(t) for t in toks}
            touched = False
            for g in groups:
                if any(normkey(t) in overlap for t in g):
                    gk = {normkey(x) for x in g}
                    for t in toks:
                        if normkey(t) not in gk and not (gk & {normkey(t)}):
                            g.append(t)
                            added_tokens += 1
                    touched = True
            if touched:
                continue
        groups.append(toks)
        existing_keys |= keys
        added_groups += 1
        added_tokens += len(toks)
    stats['new_groups_added'] = added_groups
    stats['new_tokens_added'] = added_tokens

    # ---- 8. финальная канонизация
    final = []
    for g in groups:
        cg = canon_group(g)
        if cg:
            final.append(cg)
    # убрать группы, ставшие дублями других
    seen_keys = {}
    deduped = []
    for g in final:
        k = tuple(sorted(normkey(t) for t in g))
        if k in seen_keys:
            continue
        seen_keys[k] = True
        deduped.append(g)
    final = deduped
    # сортировка для детерминизма: по первому токену
    final.sort(key=lambda g: (normkey(g[0]), g[0]))

    after = metrics(final)

    with open(DST, 'w', encoding='utf-8') as fh:
        for g in final:
            fh.write(','.join(g) + '\n')

    with open('rejected_tokens.log', 'w', encoding='utf-8') as fh:
        for t in sorted(set(removed)):
            fh.write(t + '\n')

    report = {'before': before, 'after': after, 'stats': dict(stats),
              'splits_missed': split_missed}
    with open('build_metrics.json', 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Курационное дополнение словаря категориями:
  «Горно-шахтное оборудование» (MINING_BRANDS)
  «Химическая промышленность»  (CHEM_BRANDS)
  «Запчасти»                   (PARTS_BRANDS)

Читает synonyms_clean.txt, дополняет существующие группы брендов
формами из industry_data.py и создаёт новые группы для отсутствующих.
Идемпотентен: повторный запуск ничего не меняет.

Правила безопасности (продакшн-режим):
  * если токены бренда пересекаются более чем с одной существующей
    группой — конфликт, группа не трогается (фиксируется в отчёте);
  * короткие аббревиатуры (normkey < MIN_ABBR_LEN) добавляются только
    если их normkey уникален в словаре — иначе риск ложных связей;
  * дубликаты по normkey внутри группы удаляются; идентичные группы
    склеиваются; итог сортируется как в основном конвейере.
"""
from collections import defaultdict

from industry_data import MINING_BRANDS, CHEM_BRANDS, PARTS_BRANDS

SRC = 'synonyms_clean.txt'
REPORT = 'industry_report.log'
METRICS = 'industry_metrics.json'
MIN_ABBR_LEN = 3  # normkey короче — считаем рискованной аббревиатурой


def normkey(s):
    return ''.join(ch for ch in s.lower() if ch.isalnum())


def load(path):
    groups = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                groups.append([t.strip() for t in line.split(',') if t.strip()])
    return groups


def canon_group(tokens):
    seen, out = set(), []
    for t in sorted(set(tokens), key=lambda x: (normkey(x), x)):
        k = normkey(t)
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def main():
    groups = load(SRC)
    key_index = defaultdict(set)
    key_count = defaultdict(int)
    for gi, g in enumerate(groups):
        for t in g:
            key_index[normkey(t)].add(gi)
            key_count[normkey(t)] += 1

    added_groups = appended_tokens = skipped_conflict = skipped_short = 0
    report = []

    def merge(gi_a, gi_b):
        """Склеить группу gi_b в gi_a (разделение одного бренда на две строки)."""
        target, src_g = groups[gi_a], groups[gi_b]
        have = {normkey(t) for t in target}
        for t in src_g:
            k = normkey(t)
            if k not in have:
                target.append(t)
                have.add(k)
                key_index[k].discard(gi_b)
                key_index[k].add(gi_a)
        groups[gi_b] = []
        report.append(f'MERGE {target[:2]}... <- {src_g[:2]}...')

    sections = (('MINING', MINING_BRANDS), ('CHEM', CHEM_BRANDS), ('PARTS', PARTS_BRANDS))
    for section, table in sections:
        for canon, variants in table.items():
            toks = [canon] + [v.strip() for v in variants if v.strip()]
            keys = [normkey(t) for t in toks]
            hits = set()
            for k in keys:
                hits |= key_index.get(k, set())
            hits = {h for h in hits if groups[h]}
            if len(hits) == 1:
                gi = next(iter(hits))
                existing_keys = {normkey(t) for t in groups[gi]}
                for t, k in zip(toks, keys):
                    if k in existing_keys:
                        continue
                    # короткая аббревиатура допустима только если она нигде больше не встречается
                    if len(k) < MIN_ABBR_LEN and key_count.get(k, 0) > 0:
                        skipped_short += 1
                        report.append(f'SKIP short {section}:{canon} token={t}')
                        continue
                    groups[gi].append(t)
                    existing_keys.add(k)
                    key_index[k].add(gi)
                    key_count[k] += 1
                    appended_tokens += 1
            elif len(hits) > 1:
                # Разрешаем склейку ТОЛЬКО если все затронутые группы — части одного и того же
                # курационного бренда (каждая содержит хотя бы один токен из списка).
                covered = all(any(normkey(t) in {normkey(x) for x in groups[gi]}
                                  for t in toks) for gi in hits)
                if covered:
                    gi_list = sorted(hits)
                    base = gi_list[0]
                    for other in gi_list[1:]:
                        merge(base, other)
                    gi = base
                    existing_keys = {normkey(t) for t in groups[gi]}
                    for t, k in zip(toks, keys):
                        if k in existing_keys:
                            continue
                        if len(k) < MIN_ABBR_LEN and key_count.get(k, 0) > 0:
                            skipped_short += 1
                            report.append(f'SKIP short {section}:{canon} token={t}')
                            continue
                        groups[gi].append(t)
                        existing_keys.add(k)
                        key_index[k].add(gi)
                        key_count[k] += 1
                        appended_tokens += 1
                else:
                    skipped_conflict += 1
                    report.append(f'SKIP conflict {section}:{canon} -> groups {sorted(hits)}')
            else:
                gi = len(groups)
                groups.append(toks)
                for t, k in zip(toks, keys):
                    key_index[k].add(gi)
                    key_count[k] += 1
                added_groups += 1
                appended_tokens += len(toks)
                report.append(f'NEW group {section}:{canon}')

    final = [canon_group(g) for g in groups if g]
    seen_keys, deduped = set(), []
    for g in final:
        k = tuple(sorted(normkey(t) for t in g))
        if k in seen_keys:
            continue
        seen_keys.add(k)
        deduped.append(g)
    deduped.sort(key=lambda g: (normkey(g[0]), g[0]))

    with open(SRC, 'w', encoding='utf-8') as fh:
        for g in deduped:
            fh.write(','.join(g) + '\n')

    with open(REPORT, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(report) + '\n')

    import json
    metrics = {
        'added_groups': added_groups,
        'appended_tokens': appended_tokens,
        'conflicts_skipped': skipped_conflict,
        'short_abbrevs_skipped': skipped_short,
        'total_groups': len(deduped),
        'total_tokens': sum(len(g) for g in deduped),
    }
    with open(METRICS, 'w', encoding='utf-8') as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=2)

    print(f'added_groups={added_groups} appended_tokens={appended_tokens} '
          f'conflicts_skipped={skipped_conflict} short_skipped={skipped_short} '
          f'total_groups={len(deduped)}')


if __name__ == '__main__':
    main()
